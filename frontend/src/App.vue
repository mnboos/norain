<script setup lang="ts">
import { VueQueryDevtools } from "@tanstack/vue-query-devtools";
</script>

<template>
    <q-layout view="hHh LpR lFf">
        <q-header elevated>
            <q-toolbar>
                <q-toolbar-title>NoRain</q-toolbar-title>
                <q-tabs>
                    <q-route-tab to="/" label="Dashboard" />
                    <q-route-tab to="/map" label="Karte" />
                </q-tabs>
            </q-toolbar>
        </q-header>
        <q-page-container>
            <router-view />
            <VueQueryDevtools />
        </q-page-container>
    </q-layout>
</template>
