# Memobird (local) for Home Assistant

Prints on a Memobird thermal printer through its **local HTTP API**: no cloud, no access key, no account binding.

The official open API (`open.memobird.cn`) needs an access key and a device bound to that key, and it often fails with `咕咕机未激活或者未绑定` ("device not activated or not bound") even when the printer works fine from the app. This integration skips the cloud entirely and talks to the printer on your LAN.

## Installation (HACS)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/Tozapid/ha-memobird-local`, type **Integration**.
2. Install **Memobird (local)** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Memobird (local)** and enter the printer's IP address.

Give the printer a fixed IP (DHCP reservation) in your router.

## Usage

The integration creates:

- `notify.memobird`: a notify entity, so `notify.send_message` works (message and title).
- `binary_sensor.memobird_connectivity`: whether the printer answers on the LAN.
- The `memobird_local.print` action, which gives more control over the layout:

```yaml
action: memobird_local.print
target:
  entity_id: notify.memobird
data:
  title: Список покупок
  message: |
    - молоко
    - хлеб
  bold: false
  big: false
  timestamp: true
  separator: true
```

### Formatting: Markdown and HTML

The printer can only style a whole block of text. To highlight single words, set `format: markdown` or `format: html`: the text is laid out with bundled DejaVu fonts and printed as an image.

```yaml
action: memobird_local.print
target:
  entity_id: notify.memobird
data:
  format: markdown
  message: |
    # Покупки
    Купить **молоко** и *хлеб*, ++срочно++, ~~не сыр~~, код `A-17`
    - яйца
    - масло
```

| Markdown | HTML | Result |
|---|---|---|
| `**bold**`, `__bold__` | `<b>`, `<strong>` | bold |
| `*italic*`, `_italic_` | `<i>`, `<em>` | italic |
| `++underline++` | `<u>`, `<ins>` | underline |
| `~~strike~~` | `<s>`, `<del>` | strikethrough |
| `` `code` `` | `<code>` | monospace |
| `# h1`, `## h2`, `### h3` | `<h1>`–`<h3>`, `<big>`, `<small>` | sizes |
| `- item`, `1. item` | `<ul>`/`<ol>` + `<li>` | lists |
| `---` | `<hr>` | rule |
| | `<center>` | centred text |

Line breaks are kept, as in Telegram. Use `\*` to print a literal `*` in Markdown and `&lt;` for `<` in HTML. Emoji are dropped because the fonts don't have them.

The default format for `notify.send_message` (and for actions without `format`) and the base font size (24 px by default, 12–64) are set in the integration's **Configure** dialog. Both actions also accept `font_size` to override it for one printout; headings scale with it. `print_image` also accepts `format` for its caption.

### Images

`memobird_local.print_image` prints an image from a file, a URL or a camera snapshot (use exactly one). The image is scaled to the paper width (384 dots) and converted to black and white; tall images are fine.

```yaml
action: memobird_local.print_image
target:
  entity_id: notify.memobird
data:
  camera: camera.front_door        # or file: /config/www/photo.jpg, or url: https://…
  title: Кто-то у двери
  caption: "{{ now().strftime('%H:%M') }}"
  dither: true                     # false for logos, QR codes and line art
```

Files must be inside `allowlist_external_dirs`; `/config/www` and `/media` are allowed by default.

### Text encoding

Text is sent in GBK, the printer's only encoding. Latin, Cyrillic and Chinese print fine; characters GBK can't encode (such as emoji) print as `?`.

## Local API

For reference, this is what the integration sends:

```bash
# status
curl http://PRINTER_IP/sys/printer
# → {"command":5,"printerState":3,"busy":0}

# print: basetext is base64 of GBK-encoded text; printID must be new for every job
curl -X POST http://PRINTER_IP/sys/printer -H 'Content-Type: application/json' \
  --data-binary '{"command":3,"content":{"textList":[{"encodeType":0,"printType":1,"basetext":"SGVsbG8K","fontSize":1,"bold":0,"underline":0}]},"encryptFlag":0,"hasHead":0,"hasSignature":0,"hasTail":0,"isFromDirectPrint":false,"msgType":1,"pkgCount":1,"pkgNo":1,"printID":123456,"priority":0,"result":0,"scripType":3}'
```

The printer ignores a job whose `printID` it has already printed, so the integration generates a new one for every job.

Images use `"printType":5` with `basetext` set to a base64 1-bit BMP, 384 dots wide and flipped vertically (the printer reads rows top-down). The firmware answers HTTP 500 to request bodies above roughly 26 KB, so larger jobs are sent as several packages with the same `printID` and `pkgNo` from 1 to `pkgCount`; the printer assembles them into one printout.

Fonts in `custom_components/memobird_local/fonts` are DejaVu fonts, distributed under their own license (see `LICENSE-DejaVu.txt` there).
