"""Build demo/static/vendor/lucide/ from the Lucide icons the interface uses.

The interface names its icons in three places: icons.svg#NAME in the page template, ui.icon('NAME')
and '#NAME' href swaps in the scripts, and vendor/lucide/NAME.svg in the stylesheets. This script
collects those names, copies each shape from an unpacked lucide-static package and writes the
sprite, the standalone files, LICENSE and README.txt. Shapes are copied unchanged.

    npm pack lucide-static@0.469.0 && tar xzf lucide-static-0.469.0.tgz
    python tools/build_icons.py package/icons package
"""
import pathlib
import re
import sys

VERSION = '0.469.0'
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / 'demo/static/vendor/lucide'
MUTED = '#65768d'   # --gray-500 in tokens.css; a CSS background cannot use currentColor


def used_names(available):
    names = set()
    page = (ROOT / 'demo/templates/index.html').read_text(encoding='utf-8')
    names |= set(re.findall(r'icons\.svg#([a-z0-9-]+)', page))
    for script in (ROOT / 'demo/static').glob('*.js'):
        text = script.read_text(encoding='utf-8')
        for call in re.findall(r'\bicon\(([^)]*)\)', text):
            names |= set(re.findall(r"'([a-z0-9-]+)'", call))
        names |= set(re.findall(r"'#([a-z0-9-]+)'", text))
    standalone = set()
    for sheet in (ROOT / 'demo/static').glob('*.css'):
        standalone |= set(re.findall(r'vendor/lucide/([a-z0-9-]+)\.svg', sheet.read_text(encoding='utf-8')))
    return sorted(names & available), sorted(standalone - {'icons'})


def shape(source):
    text = re.sub(r'<!--.*?-->', '', source.read_text(encoding='utf-8'), flags=re.S)
    inner = re.search(r'<svg\b[^>]*>(.*?)</svg>', text, re.S).group(1)
    return re.sub(r'\s+', ' ', inner).strip().replace(' />', '/>')


def main(folders):
    folders = [pathlib.Path(f) for f in folders]
    files = {p.stem: p for f in folders for p in f.glob('*.svg')}
    icons, standalone = used_names(set(files))
    missing = sorted({n.removesuffix('-muted') for n in standalone} - set(files))
    if missing:
        sys.exit(f'not in the package: {missing}')
    licence = next(f / 'LICENSE' for f in folders if (f / 'LICENSE').exists()).read_text(encoding='utf-8')
    OUT.mkdir(parents=True, exist_ok=True)
    symbols = '\n'.join(f'<symbol id="{n}" viewBox="0 0 24 24">{shape(files[n])}</symbol>' for n in icons)
    (OUT / 'icons.svg').write_text(
        f'<!-- Lucide {VERSION} (ISC, see LICENSE in this folder): shapes copied unchanged. -->\n'
        f'<svg xmlns="http://www.w3.org/2000/svg">\n{symbols}\n</svg>\n', encoding='utf-8')
    notes = []
    for name in standalone:
        base = name.removesuffix('-muted')
        body = shape(files[base])
        stroke = MUTED if name.endswith('-muted') else 'currentColor'
        (OUT / f'{name}.svg').write_text(
            f'<!-- Lucide {VERSION} {base} (ISC, see LICENSE in this folder) -->\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
            f'stroke="{stroke}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{body}</svg>\n', encoding='utf-8')
        if name.endswith('-muted'):
            notes.append(f'{name}.svg is {base} with the stroke colour set to {MUTED}, for a CSS background.')
    (OUT / 'LICENSE').write_text(licence, encoding='utf-8')
    (OUT / 'README.txt').write_text(
        f'Lucide {VERSION} (ISC, see LICENSE), icon shapes from the npm package lucide-static@{VERSION} (icons/*.svg).\n'
        'icons.svg holds the icons the interface uses as <symbol> elements; the shapes are unchanged, the\n'
        'stroke settings live in CSS (.icon in theme.css). The other files are single icons for CSS masks\n'
        'and backgrounds. ' + ' '.join(notes) + '\n'
        'Rebuilt by tools/build_icons.py, which keeps only the icons the page, scripts and styles name.\n'
        f'Icons ({len(icons)}): {", ".join(icons)}\n', encoding='utf-8')
    print(f'{len(icons)} symbols, {len(standalone)} standalone files')


if __name__ == '__main__':
    main(sys.argv[1:])
