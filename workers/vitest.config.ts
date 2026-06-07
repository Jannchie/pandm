import path from 'node:path'
import { defineWorkersConfig, readD1Migrations } from '@cloudflare/vitest-pool-workers/config'

export default defineWorkersConfig(async () => {
  const migrations = await readD1Migrations(path.join(__dirname, 'migrations'))
  return {
    test: {
      setupFiles: ['./test/apply-migrations.ts'],
      poolOptions: {
        workers: {
          main: './src/index.ts',
          // tests use unique ids instead of per-test storage snapshots
          isolatedStorage: false,
          singleWorker: true,
          miniflare: {
            compatibilityDate: '2025-09-01',
            d1Databases: ['DB'],
            r2Buckets: ['MEDIA'],
            bindings: {
              GITHUB_CLIENT_ID: 'cid',
              GITHUB_CLIENT_SECRET: 'csecret',
              PANDM_SECRET_KEY: 'test-secret',
              TEST_MIGRATIONS: migrations,
            },
          },
        },
      },
    },
  }
})
