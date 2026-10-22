#!/usr/bin/env python3
"""Generate profile SVGs from repository language bytes and contribution activity.

Refresh locally with uv and an authenticated GitHub CLI:
    PROFILE_USERNAME=r0h4dj1 GITHUB_TOKEN="$(gh auth token)" \\
        uv run --no-project --python 3.14 scripts/generate.py

Only owned public, non-fork, non-archived repositories are counted by default,
excluding the profile repository. Language proportions measure code bytes.
For owned private repositories, set INCLUDE_PRIVATE=true and PROFILE_TOKEN
to a credential that can read their metadata. Only aggregate data is published.
"""
import collections
import html
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

USER = os.getenv('PROFILE_USERNAME', 'YOUR_USERNAME')
TOKEN = os.getenv('PROFILE_TOKEN') or os.getenv('GITHUB_TOKEN', '')
INCLUDE_PRIVATE = os.getenv('INCLUDE_PRIVATE', 'false').lower() == 'true'
OUT = Path('assets')
COLORS = ['#3d8b70','#6ba48e','#94bdae','#abcac1','#bbc4ce','#d3c7ad','#9aadc2','#a3a3a3']


def api(url, data=None):
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'minimal-profile-poster', 'X-GitHub-Api-Version': '2022-11-28'}
    if TOKEN:
        headers['Authorization'] = 'Bearer ' + TOKEN
    if data is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(data).encode()
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Actions logs are public here; URLs and response bodies can name private repos.
        raise RuntimeError(
            f'GitHub API returned HTTP {exc.code}; check token access and expiry'
        ) from None


def repositories():
    if INCLUDE_PRIVATE:
        if not os.getenv('PROFILE_TOKEN'):
            raise RuntimeError('Set PROFILE_TOKEN secret before setting INCLUDE_PRIVATE=true')
        endpoint = 'https://api.github.com/user/repos?affiliation=owner&visibility=all&per_page=100&page='
    else:
        endpoint = 'https://api.github.com/users/' + urllib.parse.quote(USER) + '/repos?type=owner&per_page=100&page='
    items = []
    for page in range(1, 101):
        batch = api(endpoint + str(page))
        if not isinstance(batch, list):
            raise RuntimeError('Unexpected repositories response')
        items.extend(batch)
        if len(batch) < 100:
            break
    return [repo for repo in items if repo['owner']['login'].lower() == USER.lower() and not repo.get('fork') and not repo.get('archived') and repo['name'].lower() != USER.lower()]


def languages():
    amounts = collections.Counter()
    repos = repositories()
    for repo in repos:
        langmap = api(repo['languages_url'])
        for lang, bytecount in langmap.items():
            amounts[lang] += int(bytecount)
    return amounts, len(repos)


def contribution_weeks():
    query = '''query($name:String!) { user(login:$name) { contributionsCollection { contributionCalendar { totalContributions weeks { contributionDays { date contributionCount } } } } } }'''
    result = api('https://api.github.com/graphql', {'query': query, 'variables': {'name': USER}})
    if result.get('errors'):
        raise RuntimeError('GitHub GraphQL request failed; check token permissions and username')
    user = result.get('data', {}).get('user')
    if not user:
        raise RuntimeError('Cannot find GitHub username: ' + USER)
    calendar = user['contributionsCollection']['contributionCalendar']
    # GitHub week boundaries are preserved; first/last may be partial.
    weeks = [sum(day['contributionCount'] for day in week['contributionDays']) for week in calendar['weeks']]
    return weeks, calendar['totalContributions']


def esc(text):
    return html.escape(str(text), quote=True)


def svg(amounts, count, weeks, total, dark=False):
    bg, fg, muted, rule, accent = ('#0d1117','#e6edf3','#8b949e','#30363d','#62b891') if dark else ('#ffffff','#1f2328','#656d76','#d8dee4','#347f62')
    width, height = 760, 320
    allbytes = sum(amounts.values())
    sortedlang = amounts.most_common()
    shown = sortedlang[:5]
    other = sum(v for _,v in sortedlang[5:])
    if other:
        shown.append(('Other',other))
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
             '<title id="title">GitHub languages and contributions</title>',
             '<desc id="desc">Generated from repository language bytes and the last year of GitHub contributions.</desc>',
             f'<rect width="{width}" height="{height}" fill="{bg}"/>',
             f'<style>text{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif}}</style>',
             f'<text x="16" y="28" font-size="15" font-weight="600" fill="{fg}">Languages</text>',
             f'<text x="744" y="27" font-size="11" text-anchor="end" fill="{muted}">BY REPOSITORY CODE VOLUME</text>']
    x = 16.0
    w = 728.0
    if shown and allbytes:
        for index,(name, val) in enumerate(shown):
            bw = w * val/allbytes
            lines.append(f'<rect x="{x:.2f}" y="44" width="{max(0,bw-1):.2f}" height="13" rx="1" fill="{COLORS[index % len(COLORS)]}"/>')
            x += bw
        for index,(name,val) in enumerate(shown):
            pct = val/allbytes*100
            label = f'{name} {pct:.0f}%'
            # Three fixed columns keep dots clear of preceding labels across fonts.
            xpos = 16 + (index % 3) * w / 3
            ypos = 83 + (index // 3) * 26
            lines.append(f'<circle cx="{xpos+4:.2f}" cy="{ypos-4}" r="4" fill="{COLORS[index % len(COLORS)]}"/>')
            lines.append(f'<text x="{xpos+13:.2f}" y="{ypos}" font-size="12" fill="{fg}">{esc(label)}</text>')
    else:
        lines.append(f'<text x="16" y="65" font-size="12" fill="{muted}">No language data found</text>')
    lines += [f'<line x1="16" y1="134" x2="744" y2="134" stroke="{rule}"/>',
              f'<text x="16" y="161" font-size="15" font-weight="600" fill="{fg}">Activity</text>',
              f'<text x="744" y="160" font-size="11" text-anchor="end" fill="{muted}">LAST 12 MONTHS</text>']
    # Normalize to graph height; each point is one calendar week.
    graph_x, graph_y, graph_w, graph_h = 16, 184, 728, 76
    maximum = max(weeks) if weeks else 0
    for n in range(3):
        gy = graph_y + n*graph_h/2
        lines.append(f'<line x1="{graph_x}" y1="{gy:.1f}" x2="{graph_x+graph_w}" y2="{gy:.1f}" stroke="{rule}" opacity="0.65"/>')
    if weeks:
        points = []
        for i, value in enumerate(weeks):
            px = graph_x + graph_w * i/max(1,len(weeks)-1)
            py = graph_y + graph_h - (value/max(1,maximum))*graph_h
            points.append(f'{px:.1f},{py:.1f}')
        lines.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{accent}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>')
    lines += [f'<text x="16" y="283" font-size="11" fill="{muted}">52 WEEKS AGO</text>',
              f'<text x="744" y="283" font-size="11" text-anchor="end" fill="{muted}">NOW</text>',
              f'<text x="16" y="306" font-size="11" fill="{muted}">{count} repositories analysed · {total:,} contributions · GitHub data</text>',
              '</svg>']
    return '\n'.join(lines)+'\n'


def main():
    if USER == 'YOUR_USERNAME':
        raise SystemExit('Set PROFILE_USERNAME in the workflow or environment')
    amounts, count = languages()
    weeks,total = contribution_weeks()
    OUT.mkdir(exist_ok=True)
    (OUT/'profile-light.svg').write_text(svg(amounts,count,weeks,total), encoding='utf-8')
    (OUT/'profile-dark.svg').write_text(svg(amounts,count,weeks,total,dark=True), encoding='utf-8')
    print(f'Generated poster from {count} repositories and {len(weeks)} calendar weeks')


if __name__ == '__main__':
    main()
