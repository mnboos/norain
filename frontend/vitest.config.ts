import { fileURLToPath } from 'node:url'
import { mergeConfig, defineConfig, configDefaults } from 'vitest/config'
import viteConfig from './vite.config'

// vite.config.ts is a function of the Vite command, so resolve it first. Tests run under
// the build branch, which keeps the src/pages watcher and the devtools plugin out of them.
const resolvedViteConfig = viteConfig({ command: 'build', mode: 'test' })

export default mergeConfig(
  resolvedViteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      exclude: [...configDefaults.exclude, 'e2e/**'],
      root: fileURLToPath(new URL('./', import.meta.url)),
    },
  }),
)
