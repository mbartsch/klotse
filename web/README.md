# Klotse website

A static user guide with screenshots. It is plain HTML and CSS, with no scripts and no build step.

- `index.html`: the guide.
- `screenshots/`: the pictures the guide uses. Their data is invented.

## View it locally

Open `web/index.html` in a browser. Everything is relative, so it also works from any folder.

## Publish

Point GitHub Pages at the `web/` folder, or copy the folder to any static host. Nothing else is needed.

## Regenerate the new screenshots

From the checkout, run:

```sh
PYTHONPATH=src python scripts/capture_guide_screenshots.py
```

The script renders the main window, the Details YAML view, the Source… dialog, the Port forwards tab and the Metrics query window, offscreen and with invented data. It writes them to `web/screenshots/`. The older screenshots (`08`–`11`) are copies of the ones in `screens/`, which `scripts/capture_screenshots.py` produces.

Screenshots of a real cluster would show real names and data, so keep them out of the repository.
