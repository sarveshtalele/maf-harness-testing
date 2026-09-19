const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

// Ensure output directory exists
const outDir = path.resolve(__dirname, '../../../../output/coverage');
if (!fs.existsSync(outDir)) {
  fs.mkdirSync(outDir, { recursive: true });
}

const rootDir = path.resolve(__dirname, '../../../../');

console.log('Running Playwright tests under nyc coverage...');
execSync(
  `npx nyc --silent --reporter=json-summary --report-dir=${outDir} playwright test output/automation/playwright.spec.ts --config=playwright.config.ts`,
  { stdio: 'inherit', cwd: rootDir }
);

console.log('✅ Coverage report generated at', outDir);
