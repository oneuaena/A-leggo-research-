# Fonts used in generated research reports

- Noto Sans SC: official Google Fonts repository, https://github.com/google/fonts/tree/main/ofl/notosanssc . `NotoSansSC-Regular.ttf` is the weight-400 static instance of the upstream variable font; the glyph designs are unchanged. Distributed under the accompanying SIL Open Font License 1.1.
- DejaVu Sans: supplied by the declared Matplotlib dependency, https://dejavu-fonts.github.io/ . Distributed under the accompanying Bitstream Vera / DejaVu license.

Reports embed subsets of both fonts. Latin and mathematical characters use DejaVu Sans; CJK characters use Noto Sans SC. Characters outside both fonts are shown as explicit Unicode code points with a coverage notice; the Markdown report preserves the original text.
