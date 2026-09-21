# Brisavia website assets

- `logo.svg` / `logo.png`: general horizontal logo for light backgrounds.
- `logo-light.svg` / `logo-light.png`: white wordmark for dark backgrounds.
- `mark-master.png`: full-resolution generated source artwork.
- `favicon.ico`: 16, 32 and 48 pixel browser icon; also copied to `/favicon.ico`.
- `favicon-16.png` / `favicon-32.png`: small browser icons.
- `apple-touch-icon.png`: 180 pixel Apple home-screen icon.
- `icon-192.png` / `icon-512.png`: web app and notification icons.
- `icon-maskable-512.png`: extra padding for launcher masks.
- `preview.html`: view all assets, including actual favicon sizes.

The website retains its existing navy #1b365d, blue #2d5a8e and grey #e6ebf1.
The artwork adds a gold sun from the supplied logo. Generated pixels may vary
slightly from these exact CSS values. SVG wordmarks embed raster artwork;
they are not vector masters. Their text uses Arial/sans-serif; the live header
keeps the site's existing font.

Created with the built-in imagegen tool using the supplied logo as a visual
reference and the website screenshot as a palette reference. Final refinement
prompt: Keep the road, wind and sun composition; remove glow, shading and
bevels; use solid white shapes, muted gold #e5b95c sun and opaque navy #1b365d
background; no text; generous clear space. The generated artwork is retained
as delivered; packaging only resizes it, adds maskable padding and wordmarks.

To rebuild derived files with Sharp available:

```sh
node scripts/build-brand.cjs
# Alternatively pass an installed Sharp module path as the first argument.
```
