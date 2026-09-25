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
- The `memobird_local.print` action, which gives more control over formatting:

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
