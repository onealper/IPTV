"""Build a personal playlist from current Free-TV country playlists."""

import json
import os
import re
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
SOURCE = "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_{}.m3u8"


def download(country):
    return download_url(SOURCE.format(country))


def download_url(url):
    if not url.startswith(("https://", "http://")):
        raise ValueError("Playlist sources must be HTTP(S) URLs")
    request = Request(url, headers={"User-Agent": "Personal-IPTV/1.0"})
    with urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8-sig")


def attribute(line, key):
    match = re.search(r'\b' + re.escape(key) + r'="([^"]*)"', line)
    return match.group(1) if match else ""


def channel_name(line):
    name = attribute(line, "tvg-name") or re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', line, maxsplit=1)[-1]
    return re.sub(r"\s*[Ⓐ-ⓩ]+\s*$", "", name).strip().casefold()


def identity(line):
    name = re.sub(r"\([^)]*\)|\[[^]]*\]", "", channel_name(line))
    name = unicodedata.normalize("NFKD", name).replace("ı", "i")
    return "".join(char for char in name if char.isalnum() and not unicodedata.combining(char))


def parse_playlist(content):
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise ValueError("Source is not an M3U playlist")
    records, pending = [], []
    for line in lines[1:]:
        if line.startswith("#EXTINF:"):
            if pending:
                raise ValueError("Channel is missing its stream URL")
            pending = [line]
        elif line.startswith("#"):
            if pending:
                pending.append(line)
        else:
            if not pending or not line.startswith(("https://", "http://")):
                raise ValueError("Unexpected or unsupported stream URL")
            records.append(pending + [line])
            pending = []
    if pending or not records:
        raise ValueError("Source playlist is incomplete or empty")
    return lines[0], records


def safe_text(value):
    text = str(value)
    if any(char in text for char in ('"', "\n", "\r")):
        raise ValueError("Channel fields cannot contain quotes or line breaks")
    return text


def normalize_imported(record, group, country_code):
    """Use the same channel attribute order as the Free-TV generator."""
    original = record[0]
    display_name = re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', original, maxsplit=1)[-1].strip()
    tvg_name = attribute(original, "tvg-name") or display_name
    tvg_name = re.sub(r"\s*[Ⓐ-ⓩ]+\s*$", "", tvg_name).strip()
    fields = [
        ('tvg-name', tvg_name),
        ('tvg-logo', attribute(original, "tvg-logo")),
        ('tvg-id', attribute(original, "tvg-id")),
        ('tvg-country', country_code),
    ]
    for key in ('tvg-chno', 'http-user-agent', 'http-referrer'):
        value = attribute(original, key)
        if value:
            fields.append((key, value))
    fields.append(('group-title', group))
    attributes = " ".join(f'{key}="{safe_text(value)}"' for key, value in fields)
    return [f'#EXTINF:-1 {attributes},{safe_text(display_name)}'] + record[1:]


def build(settings, fetch=download, fetch_url=download_url):
    countries = settings["countries"]
    if not isinstance(countries, list) or not countries:
        raise ValueError("Choose at least one country")
    if any(not isinstance(country, str) or not re.fullmatch(r"[a-z_]+", country) for country in countries):
        raise ValueError("Use country file names such as turkey and sweden")
    countries = list(dict.fromkeys(countries))
    excluded = {str(name).strip().casefold() for name in settings.get("exclude_channels", [])}
    output, epgs, counts = [], [], {}
    for country in countries:
        header, records = parse_playlist(fetch(country))
        for epg in attribute(header, "x-tvg-url").split(","):
            if epg.strip() and epg.strip() not in epgs:
                epgs.append(epg.strip())
        group = attribute(records[0][0], "group-title") or country.replace("_", " ").title()
        code = attribute(records[0][0], "tvg-country")
        seen_names = {identity(record[0]) for record in records}
        seen_urls = {record[-1] for record in records}
        for source in settings.get("extra_playlists", []):
            if source["country"] != country:
                continue
            _, imported = parse_playlist(fetch_url(source["url"]))
            for record in imported:
                key = identity(record[0])
                if key in seen_names or record[-1] in seen_urls:
                    continue
                records.append(normalize_imported(record, group, code))
                seen_names.add(key)
                seen_urls.add(record[-1])
        extras = []
        for channel in settings.get("extra_channels", []):
            if channel["country"] != country:
                continue
            name = safe_text(channel["name"]).strip()
            url = safe_text(channel["url"]).strip()
            if not name or not url.startswith(("https://", "http://")):
                raise ValueError("Extra channels need a name and an HTTP(S) stream URL")
            line = (f'#EXTINF:-1 tvg-name="{name}" tvg-id="{safe_text(channel.get("epg_id", ""))}" '
                    f'tvg-logo="{safe_text(channel.get("logo", ""))}" tvg-country="{code}" '
                    f'group-title="{group}",{name}')
            extras.append([line, url])
        excluded_keys = {identity('#EXTINF:-1,' + name) for name in excluded}
        overrides = {identity(record[0]) for record in extras}
        kept = [record for record in records if identity(record[0]) not in excluded_keys | overrides]
        kept.extend(record for record in extras if identity(record[0]) not in excluded_keys)
        counts[country] = len(kept)
        output.extend(line for record in kept for line in record)
    header = '#EXTM3U x-tvg-url="' + ", ".join(epgs) + '"'
    return "\n".join([header] + output) + "\n", counts


def main():
    settings = json.loads((ROOT / "custom-settings.json").read_text(encoding="utf-8"))
    playlist, counts = build(settings)
    temporary = ROOT / "custom.m3u8.tmp"
    temporary.write_text(playlist, encoding="utf-8")
    os.replace(temporary, ROOT / "custom.m3u8")
    for country, count in counts.items():
        print(f"{country}: {count} channels")
    print(f"Total: {sum(counts.values())} channels")


if __name__ == "__main__":
    main()
