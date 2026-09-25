import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
	plugins: [react()],
	server: { watch: { ignored: ['**/.venv/**', '**/backend/smartpark.db', '**/backend/tests/test.db'] } },
})