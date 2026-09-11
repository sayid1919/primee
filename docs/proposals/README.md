# Proposals

Client-facing proposals built from the audit reports in `docs/audits/`.

## sorud-info-proposal-2026-09-11

An executable redesign proposal for <https://sorud.info>, written in Persian and
laid out as a 24-page A4 document. Every recommendation cites a problem ID
(`S-01` .. `S-20`) from `docs/audits/sorud-info-audit-2026-09-11.md`, and every
recommendation carries two options: a minimum acceptable one and an ideal one.

| File | What it is |
| --- | --- |
| `sorud-info-proposal-2026-09-11.pdf` | The deliverable. Fonts are embedded, so it renders identically anywhere. |
| `sorud-info-proposal-2026-09-11.html` | The source the PDF is printed from. |

### Rebuilding the PDF

The HTML references four typefaces through relative `fonts/*.ttf` paths. Those
font files are **not** committed here, because their redistribution licences
have not been verified. Put them in a `fonts/` directory beside the HTML:

```
fonts/Azar.ttf            display headings and the wordmark
fonts/Darvish-Light.ttf   body text, weight 300
fonts/Darvish-Regular.ttf body text, weight 400
fonts/Darvish-Medium.ttf  body text, weight 500
fonts/Darvish-SemiBold.ttf
fonts/Darvish-Bold.ttf
fonts/Mirza-Regular.ttf   decorative siah-mashq watermarks only, never running text
fonts/Mirza-Bold.ttf
```

Then print it with a headless Chromium:

```sh
chromium --headless=new --no-pdf-header-footer \
  --print-to-pdf=sorud-info-proposal-2026-09-11.pdf \
  "file://$PWD/sorud-info-proposal-2026-09-11.html"
```

Without the font files the page still renders, but falls back to system faces
and the line breaks will differ from the committed PDF.

### Review modes built into the HTML

Open the file with a query string to inspect it without printing:

- `?review` lays all 24 pages out as a contact sheet.
- `?page=12` renders a single page at A4 size.
