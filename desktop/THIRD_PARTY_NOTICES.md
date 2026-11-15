# Third-Party Notices

The desktop shell depends on the Rune UI toolkit and follows the structure of the Hex
desktop app. Both are MIT licensed, and both carry the same notice, reproduced below as
read from each LICENSE file.

## Rune

- Crates used: `rune`, `rune-kit`, `rune-theme-opencode` (and their workspace
  dependencies, which Cargo resolves from the same repository).
- Source: https://github.com/anomalyco/rune.git at revision
  `c8f2888e768dc41747e1b8656b06631b8c47fdab`.
- License: MIT, from the `LICENSE` file at the repository root at that revision.
- Fonts and other assets that `rune-theme-opencode` embeds ship under that crate's own
  terms inside the Rune repository; check them before redistributing a packaged build.

## Hex

- Used as a pattern only: this crate set, the pinned Rune revision, the lint block and the
  window and theme setup approach. No Hex source code is copied into this repository.
- Source: https://github.com/anomalyco/hex (repository field of Hex's `Cargo.toml`).
- License: MIT, from the `LICENSE` file at the root of the Hex desktop app, with the same
  text and copyright holder as Rune's.

## License text (Rune and Hex)

```text
MIT License

Copyright (c) 2026 Christopher Langton

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Other dependencies

Crates from crates.io (`serde`, `serde_json`, `ureq`, `keyring`, `getrandom`,
`color-eyre`, and their dependencies) keep their own licenses, listed in each crate.
