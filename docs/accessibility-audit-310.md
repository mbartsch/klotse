# Harbour accessibility review (#310)

Reviewed against the current built-in Harbour theme using its declared colors and offscreen Qt tests. Contrast ratios below are calculated from sRGB relative luminance; they are not measurements of rendered pixels, antialiasing, transparency or user-supplied themes.

| Pair | Ratio |
| --- | ---: |
| Text / base | 14.53:1 |
| Muted / base | 6.78:1 |
| Muted / window | 6.23:1 |
| Text / selected row | 7.24:1 |
| White / accent | 4.80:1 |
| Input border / base | 3.43:1 |
| Focus accent / base | 6.98:1 |
| Status: OK / window | 7.24:1 |
| Status: error / window | 6.36:1 |
| Status: warning / window | 7.86:1 |

The previously hard-coded plugin error red had 4.10:1 against the window background. It now uses the theme error color; text labels also communicate errors independently of color.

Automated checks exercise the overview card Space action and chart arrow/Enter interaction, accessible chart description, focus eligibility, and theme color pairs. Existing resource table, theme and chart tests cover additional behavior. Source inspection found explicit focus drawing for the navigator, chart and overview cards and text in the plugin error status action.

Still requires a live display and assistive-technology review: visual focus on all controls, keyboard-only traversal of tables, menus, cards, charts and dialogs; screen reader announcements; clipping and contrast at common window sizes and high-DPI scales; and contrast of custom themes, translucent or painted content. The offscreen Qt environment has no font directory, so it cannot validate rendered text or visual focus. This review does not establish full accessibility compliance.
