# METU-TRAIL website

Source of <https://metu-trail.github.io>, the website of the METU Trustworthy and Responsible AI Lab.
GitHub Pages rebuilds the site automatically about a minute after every change to `main`.

## Editing on GitHub (no HTML needed)

Open a file on GitHub, click the pencil icon, edit, and press **Commit changes**.

| To change…                              | Edit this file                          |
|-----------------------------------------|-----------------------------------------|
| Home page text                          | `index.md`                              |
| Research page introduction              | `research.md`                           |
| Research theme descriptions             | `_data/themes.yml`                      |
| PIs (names, links, photos)              | `_data/pis.yml`                         |
| Graduate students and past members      | `_data/members.yml`                     |
| Publications (fix a title, add a theme) | `_data/publications.yml`                |
| Papers deliberately hidden              | `_data/publications_excluded.yml`       |
| Courses                                 | `_data/courses.yml`                     |
| Address, e-mail, menu                   | `_config.yml`                           |
| Logo                                    | replace `assets/img/logo.svg`           |

`.md` files are Markdown: `**bold**`, `*italic*`, `[link text](https://…)`, and `- ` for bullet lists.
`.yml` files are lists of entries. Keep the indentation, and copy an existing entry to add a new one.
Photos go in `assets/img/people/` (upload them with **Add file → Upload files**). Then put the
file name in the `photo:` field.

A publication entry looks like this:

```yaml
- title: Uncertainty as a Fairness Measure
  authors: [S Kuzucu, J Cheong, H Gunes, Sinan Kalkan]
  venue: Journal of Artificial Intelligence Research
  year: 2024
  url: https://…
  themes: [fairness, robustness]   # fairness, privacy, explainability, robustness, alignment
  locked: true                     # optional: the update script will never touch this entry
```

A PI name in `authors` is highlighted and linked when it matches the PI's `name` or one of the
`aliases` in `_data/pis.yml`.

## Updating publications and courses

```bash
pip3 install -r scripts/requirements.txt   # once
python3 scripts/update.py
```

The script:

1. fetches every PI's Google Scholar profile, falling back to DBLP if Scholar blocks the request;
2. merges duplicate papers (the same title from several PIs, or an arXiv version and the published version);
3. shows **metadata updates** for papers already on the site, such as a preprint that got published,
   and asks for each one: (y)es, (n)o, (a)ll remaining, (s)kip remaining;
4. shows every **new paper** with a suggested decision (include/exclude, plus themes) and asks:
   (y) include, (n) exclude and remember, (l) ask again next time, (t) include with themes you type,
   (a) accept the suggestions for all remaining papers;
5. compares the course details with the METU catalog and asks before changing anything;
6. shows the changed files, then asks before it **commits** and **pushes** to GitHub.

Options: `--dry-run` shows changes without writing, `--only pubs` or `--only courses` limits
what is checked, and `--no-push` commits without pushing.

Only papers on trustworthy and responsible AI are listed. Decisions you make are stored in
`_data/publications.yml` (included) and `_data/publications_excluded.yml` (hidden), so each paper
is asked about only once. To reconsider a hidden paper, delete its entry from the excluded file.

If Google Scholar starts blocking the script, wait a few hours or set each PI's `dblp_pid` in
`_data/pis.yml`. The pid is the part after `dblp.org/pid/` on the PI's DBLP page.

## Previewing locally (optional)

```bash
bundle install
LC_ALL=en_US.UTF-8 bundle exec jekyll serve   # then open http://localhost:4000
```

On macOS, if `eventmachine` fails to compile during `bundle install`, run:

```bash
MAKEFLAGS="CXX=clang++\ -isysroot\ $(xcrun --show-sdk-path)\ -I$(xcrun --show-sdk-path)/usr/include/c++/v1" bundle install
```
