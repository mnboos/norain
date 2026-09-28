// @ts-check

import js from "@eslint/js";
import ts from "typescript-eslint";
import prettierConfig from "@vue/eslint-config-prettier/skip-formatting";
import vueParser from "vue-eslint-parser";
import eslintPluginVueQuery from "@tanstack/eslint-plugin-query";
import eslintPluginVue from "eslint-plugin-vue";
import { defineConfigWithVueTs } from "@vue/eslint-config-typescript";
import vueI18n from "@intlify/eslint-plugin-vue-i18n";

export default defineConfigWithVueTs(
    js.configs.recommended,
    ...eslintPluginVueQuery.configs["flat/recommended"],
    ...ts.configs.strictTypeChecked,
    ...ts.configs.stylisticTypeChecked,

    ...eslintPluginVue.configs["flat/recommended"],
    { ignores: ["src/api/**/*", "src/locales/*", "dist/**", "typed-router.d.ts"] },
    {
        files: ["src/pages/**/*.vue"],
        rules: {
            // File-based routes intentionally use names such as index.vue and [id].vue.
            "vue/multi-word-component-names": "off",
        },
    },
    {
        // The catalogs in src/locales are checked by the build (unplugin-vue-i18n compiles
        // every message) and by src/locales/__tests__; these rules check the code that uses them.
        files: ["src/**/*.{ts,vue}"],
        plugins: { "@intlify/vue-i18n": vueI18n },
        rules: {
            "@intlify/vue-i18n/no-missing-keys": "error",
            // Text shown to users goes through t(); brand names and units are the exceptions.
            "@intlify/vue-i18n/no-raw-text": [
                "warn",
                {
                    ignorePattern: "^[-–—·•:,.()/+|→↑➤°%#!?\\s\\d]*$",
                    ignoreText: [
                        ...["MeteoLane", "MeteoLane Plus", "MeteoLane Free", "Plus", "Free", "FAQ"],
                        ...["km", "km ·", "min", "h", "m", "m ↑", "°C", "Open-Meteo", "OpenWeatherMap"],
                    ],
                },
            ],
            "@intlify/vue-i18n/no-deprecated-tc": "error",
            "@intlify/vue-i18n/no-deprecated-v-t": "error",
        },
        settings: {
            "vue-i18n": {
                localeDir: "./src/locales/*.json",
                messageSyntaxVersion: "^11.0.0",
            },
        },
    },
    {
        // The landing page carries its own de/en copy object (and placeholders for shots to come).
        files: ["src/pages/welcome.vue"],
        rules: { "@intlify/vue-i18n/no-raw-text": "off" },
    },
    {
        languageOptions: {
            parserOptions: {
                projectService: true,
                tsconfigRootDir: import.meta.dirname,
            },
        },
        rules: {
            "no-undef": "off",
            "@typescript-eslint/no-empty-interface": "warn",
            indent: "off",
            "@typescript-eslint/consistent-type-assertions": ["error", { assertionStyle: "never" }],
            "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true, allowBoolean: true }],
        },
    },
    {
        languageOptions: {
            parser: vueParser,
            parserOptions: {
                parser: ts.parser,
                extraFileExtensions: [".vue"],
                sourceType: "module",
            },
        },
    },
    {
        // Plain scripts served as they are (the service worker). No tsconfig covers them, so
        // they are linted without type information. Must come after the projectService block.
        ...ts.configs.disableTypeChecked,
        files: ["public/**/*.js"],
    },
    {
        // Standalone CommonJS build scripts are linted without a TypeScript project.
        ...ts.configs.disableTypeChecked,
        files: ["scripts/**/*.cjs"],
        languageOptions: {
            parser: ts.parser,
            sourceType: "commonjs",
            parserOptions: {
                project: false,
                projectService: false,
                sourceType: "commonjs",
            },
        },
        rules: {
            ...ts.configs.disableTypeChecked.rules,
            "@typescript-eslint/no-require-imports": "off",
        },
    },
    prettierConfig,
);
