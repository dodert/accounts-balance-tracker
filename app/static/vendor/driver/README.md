driver.js 1.14.0 (https://github.com/nilbuild/driver.js), MIT license (LICENSE here), used by the guided tour
(app/static/js/tour.js, #156). Copied unchanged from the npm package's `dist/`, never loaded from a CDN:

- `driver.min.js` = `dist/driver.js.mjs` (renamed: `.mjs` may be served without a JavaScript type)
- `driver.css` = `dist/driver.css`

To update: download `https://registry.npmjs.org/driver.js/-/driver.js-<version>.tgz`, copy those two files and the
license, update the version here, in README.md and the hashes below, and run the E2E tests.

sha256:
60352de861ce5cbf25052a58a15f23bb0d5b6308b5f6b0c23b874d72e66a994f  driver.min.js
9b47907a54c7629054ff09e5fbb7bcd139d672f69efd39df47862f337a3f9a9e  driver.css
