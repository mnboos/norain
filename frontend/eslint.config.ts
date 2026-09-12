// @ts-check

import js from "@eslint/js";
import ts from "typescript-eslint";
import prettierConfig from "@vue/eslint-config-prettier/skip-formatting";
import vueParser from "vue-eslint-parser";
import eslintPluginVueQuery from "@tanstack/eslint-plugin-query";
import eslintPluginVue from "eslint-plugin-vue";
import { defineConfigWithVueTs } from "@vue/eslint-config-typescript";

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
        rules: {
            // Optional.
            // "@intlify/vue-i18n/no-dynamic-keys": "error",
            // "@intlify/vue-i18n/no-missing-keys": "error",
            // "@intlify/vue-i18n/no-unused-keys": [
            //     "error",
            //     {
            //         extensions: [".vue", ".ts"],
            //     },
            // ],
        },
        settings: {
            "vue-i18n": {
                localeDir: "./src/locales/*.{json,json5,yaml,yml}", // extension is glob formatting!
                // or
                // localeDir: {
                //   pattern: './path/to/locales/*.{json,json5,yaml,yml}', // extension is glob formatting!
                //   localeKey: 'file' // or 'path' or 'key'
                // }
                // or
                // localeDir: [
                //   {
                //     // 'file' case
                //     pattern: './path/to/locales1/*.{json,json5,yaml,yml}',
                //     localeKey: 'file'
                //   },
                //   {
                //     // 'path' case
                //     pattern: './path/to/locales2/*.{json,json5,yaml,yml}',
                //     localePattern: /^.*\/(?<locale>[A-Za-z0-9-_]+)\/.*\.(json5?|ya?ml)$/,
                //     localeKey: 'path'
                //   },
                //   {
                //     // 'key' case
                //     pattern: './path/to/locales3/*.{json,json5,yaml,yml}',
                //     localeKey: 'key'
                //   },
                // ]

                // Specify the version of `vue-i18n` you are using.
                // If not specified, the message will be parsed twice.
                messageSyntaxVersion: "^11.0.0",
            },
        },
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
    prettierConfig,
);
