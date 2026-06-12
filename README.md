# Stremio MCP Server

MCP server pro práci se Stremio addon protokolem. Umožňuje AI asistentům (Claude Desktop, Claude Code…) vyhledávat filmy a seriály, číst metadata a dotazovat se libovolných Stremio addonů.

## Nástroje

### Addon protokol (veřejné, bez přihlášení)

| Nástroj | Popis |
|---|---|
| `stremio_search` | Vyhledá filmy/seriály podle názvu (přes oficiální Cinemeta addon) |
| `stremio_get_meta` | Detailní metadata titulu podle IMDb ID (popis, žánry, herci, režie, u seriálů přehled epizod) |
| `stremio_browse_catalog` | Procházení katalogů – populární tituly, filtrování podle žánru nebo roku, stránkování |
| `stremio_get_addon_manifest` | Načte manifest libovolného addonu (co umí, jaké má katalogy a typy) |
| `stremio_get_streams` | Získá seznam streamů pro titul z libovolného addonu (u seriálů zadej `season` + `episode`) |

### Uživatelský účet a knihovna (vyžaduje přihlášení)

| Nástroj | Popis |
|---|---|
| `stremio_login` | Přihlásí se e-mailem a heslem, uloží `authKey` pro zbytek session |
| `stremio_get_library` | Načte knihovnu přihlášeného uživatele (filtrování podle typu, stránkování) |
| `stremio_add_to_library` | Přidá film nebo seriál do knihovny (metadata stáhne automaticky z Cinemeta) |
| `stremio_remove_from_library` | Odebere titul z knihovny (soft-delete, stejně jako nativní Stremio) |

## Instalace

```bash
pip install -r requirements.txt
```

(Vyžaduje Python 3.10+.)

## Rychlý test

```bash
python stremio_mcp.py
```

Server běží přes stdio – po spuštění čeká na MCP klienta. Pro interaktivní testování použij MCP Inspector:

```bash
npx @modelcontextprotocol/inspector python stremio_mcp.py
```

## Zapojení do Claude Desktop

Do `claude_desktop_config.json` přidej:

```json
{
  "mcpServers": {
    "stremio": {
      "command": "python",
      "args": ["/absolutni/cesta/k/stremio_mcp.py"]
    }
  }
}
```

Umístění konfiguračního souboru:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

Po restartu Claude Desktop se nástroje objeví automaticky.

## Příklady použití (prompty pro asistenta)

- „Najdi mi film Inception a řekni mi o něm detaily.“
- „Jaké jsou teď populární sci-fi seriály?“
- „Načti manifest addonu https://example.com/manifest.json a zjisti, jestli umí streamy.“
- „Z addonu X mi vytáhni streamy pro Breaking Bad S02E05.“

## Autentizace

Pro nástroje pracující s knihovnou existují dvě možnosti:

**Varianta A – přihlásit se za běhu** (doporučeno pro interaktivní použití):
Zavolej nástroj `stremio_login` s e-mailem a heslem. `authKey` se uloží do paměti serveru a platí po celou dobu jeho běhu.

**Varianta B – env proměnná** (doporučeno pro automatizaci):
Nastav `STREMIO_AUTH_KEY` před spuštěním serveru:
```bash
STREMIO_AUTH_KEY=tvůj_klíč python stremio_mcp.py
```
Klíč zjistíš po přihlášení přes `stremio_login` (pole `auth_key` v odpovědi).

## Poznámky

- Identifikátorem titulů je IMDb ID (`tt1375666`). Vrací ho `stremio_search` i katalogy.
- Cinemeta poskytuje jen metadata, **ne streamy** – pro `stremio_get_streams` musíš zadat URL addonu, který resource `stream` podporuje (ověříš přes `stremio_get_addon_manifest`).
- Server podporuje i `stremio://` deep-link URL – automaticky je převede na `https://`.
- Nástroje pro čtení (vyhledávání, metadata, katalogy) nevyžadují žádný klíč.
