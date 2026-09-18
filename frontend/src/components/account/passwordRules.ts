import type { Ref } from "vue";

/**
 * A q-input rule for the "repeat password" field. Both new-password forms use it, so a
 * typo can't lock anyone out of an account they just set up.
 */
export function samePassword(password: Ref<string>) {
    return (value: string): true | string => value === password.value || "Die Passwörter stimmen nicht überein.";
}
