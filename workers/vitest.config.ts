import path from 'node:path'
import { cloudflareTest, readD1Migrations } from '@cloudflare/vitest-pool-workers'
import { defineConfig } from 'vitest/config'

// pool-workers v0.19 dropped isolatedStorage/singleWorker: storage is isolated
// per test file now, which suits a suite that already keys everything by unique id.
export default defineConfig({
  plugins: [
    cloudflareTest(async () => {
      const migrations = await readD1Migrations(path.join(__dirname, 'migrations'))
      return {
        main: './src/index.ts',
        miniflare: {
          compatibilityDate: '2025-09-01',
          d1Databases: ['DB'],
          r2Buckets: ['MEDIA'],
          durableObjects: { RUN_STORE: { className: 'RunStore', useSQLite: true } },
          bindings: {
            GITHUB_CLIENT_ID: 'cid',
            GITHUB_CLIENT_SECRET: 'csecret',
            PANDM_SECRET_KEY: 'test-secret',
            TEST_MIGRATIONS: migrations,
          },
        },
      }
    }),
  ],
  test: {
    setupFiles: ['./test/apply-migrations.ts'],
  },
})
