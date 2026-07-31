Drop the OBB logo here and it appears automatically. Nothing else in this folder is used.

  logo.svg        the full lockup (mark + BIZ BUILDERS). Shown on the sign-in screen.
  logo-mark.svg   the mark alone, no tagline. Shown in the masthead at 34px tall, where the
                  tagline would render at about five pixels.

SVG is preferred so it stays sharp at any size; a 2x PNG works too, just point
config/brand.json > logoPath / logoFullPath at the filename you used.

Until these files exist the board falls back to the "OBB" wordmark, which is the current state.
No code change is needed when you add them.
