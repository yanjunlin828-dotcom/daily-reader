# Third-party resources

The browser loads all bundled fonts from the local service. The original font files are not modified.

| Font | Upstream | License included in this repository |
| --- | --- | --- |
| Source Han Sans CN VF | [Adobe Source Han Sans](https://github.com/adobe-fonts/source-han-sans) | [SIL OFL 1.1](../web/fonts/sans-LICENSE.txt) |
| Source Han Serif CN VF | [Adobe Source Han Serif](https://github.com/adobe-fonts/source-han-serif) | [SIL OFL 1.1](../web/fonts/serif-LICENSE.txt) |
| Newsreader, Latin, weight 500 | [The Newsreader Project](https://github.com/productiontype/Newsreader) | [SIL OFL 1.1](../web/fonts/newsreader-OFL.txt) |

Download URLs, file sizes and SHA-256 checksums are recorded in [manifest.json](../web/fonts/manifest.json) and [newsreader-manifest.json](../web/fonts/newsreader-manifest.json).

The included demonstration article, its Chinese translations and reading guide were created for this project. Its characters, location and events are fictional. No third-party news article is included in this public sample library.

## Optional offline dictionary

ECDICT is obtained separately from [skywind3000/ECDICT](https://github.com/skywind3000/ECDICT), pinned in [source.json](../resources/ecdict/source.json). Its [MIT license](../resources/ecdict/LICENSE) is retained. The CSV and generated SQLite database are excluded from this repository. See [setup instructions](offline-dictionary.md).
