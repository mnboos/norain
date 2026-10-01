import { type CoverageAreaOut, CoverageAreaOutStatusEnum as Status } from "@norain/api/models";

import { type AppLocale, currentLocale, intlLocale } from "@/i18n";

/** A choice in the vote picker: a country from the server's list, or a listed region. */
export interface AreaOption {
    code: string;
    label: string;
}

/**
 * An area's name in the reader's language. A region carries the admin's names; a country is
 * named from its ISO code by the browser, from the same ICU data the server's mails use.
 */
export function areaName(
    code: string,
    area?: Pick<CoverageAreaOut, "name" | "nameEn"> | null,
    locale: AppLocale = currentLocale(),
): string {
    if (area?.name) return locale === "en" ? (area.nameEn ?? area.name) : area.name;
    if (!/^[A-Z]{2}$/.test(code)) return code;
    try {
        return new Intl.DisplayNames([intlLocale(locale)], { type: "region", fallback: "code" }).of(code) ?? code;
    } catch {
        return code;
    }
}

/**
 * The `flag-icons` file name for an area: the country's flag, also for a region below it
 * (`IT-32` → `it`). Null for a code that names no country.
 */
export function flagCode(code: string): string | null {
    return /^[A-Z]{2}(-|$)/.test(code) ? code.slice(0, 2).toLowerCase() : null;
}

/** The admin's note under a covered or planned area, in the reader's language. */
export function areaNote(area: CoverageAreaOut, locale: AppLocale = currentLocale()): string | null {
    return (locale === "en" ? (area.noteEn ?? area.note) : area.note) ?? null;
}

/** The wish list: every area not covered yet that has votes, most wanted first, then by name. */
export function rankedWishes(areas: CoverageAreaOut[], locale: AppLocale = currentLocale()): CoverageAreaOut[] {
    const collator = new Intl.Collator(intlLocale(locale));
    return areas
        // The viewer's own vote shows before the daily settlement counts it (core/coverage.py).
        .filter(area => area.status !== Status.Covered && ((area.votes ?? 0) > 0 || area.voted === true))
        .sort(
            (a, b) =>
                (b.votes ?? 0) - (a.votes ?? 0) ||
                collator.compare(areaName(a.code, a, locale), areaName(b.code, b, locale)),
        );
}

/** Everything one may vote for: the countries not covered, plus regions listed for votes. */
export function voteOptions(
    countries: string[],
    areas: CoverageAreaOut[],
    locale: AppLocale = currentLocale(),
): AreaOption[] {
    const byCode = new Map(areas.map(area => [area.code, area]));
    const codes = new Set(countries);
    for (const area of areas) {
        if (area.status === Status.Candidate || area.status === Status.Planned) codes.add(area.code);
    }
    const collator = new Intl.Collator(intlLocale(locale));
    return [...codes]
        .map(code => ({ code, label: areaName(code, byCode.get(code), locale) }))
        .sort((a, b) => collator.compare(a.label, b.label));
}
