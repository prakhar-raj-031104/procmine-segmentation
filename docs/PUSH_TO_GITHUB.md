# How to put this repository on GitHub

This bundle is a **complete git repository** — it already contains the full commit
history in its `.git` folder. You are not starting from scratch; you are just
uploading an existing repo to GitHub.

## Part 1 — Initial push (do this once)

1. **Unzip** `procmine-segmentation.zip`. You'll get a `procmine-segmentation/` folder.

2. **Open a terminal inside that folder:**
   ```bash
   cd procmine-segmentation
   git log --oneline        # confirm the commit history is present
   git tag -l                # confirm stage tags are present, e.g. stage0-parser
   ```

3. **Create an empty repository on GitHub**
   - Go to github.com → New repository
   - Name it (e.g. `procmine-segmentation`)
   - **Do NOT** add a README, .gitignore, or license (the repo already has these;
     adding them creates a conflict)
   - Click Create

4. **Connect and push (including tags):**
   ```bash
   git remote add origin https://github.com/<your-username>/procmine-segmentation.git
   git branch -M main
   git push -u origin main
   git push origin --tags
   ```

5. Refresh the GitHub page — you'll see all files, the full commit history, and
   the `stage0-parser` tag marking this milestone.

**Keep this same local folder** — every future stage builds on top of it. Do not
re-unzip a fresh copy later; you'll lose your `origin` remote and your push history.

## Part 2 — Every future stage (repeatable)

For each new stage, instead of a full repo zip, you'll be given a small
**`.bundle` file** containing only the new commits since your last push. This
keeps your push history on GitHub honest — each stage shows up as its own
push, with its own timestamp, instead of one big dump.

1. **Download** the new bundle, e.g. `stage1-annotate.bundle`, into (or next to)
   your existing `procmine-segmentation/` folder.

2. **Pull the new commits into your local repo:**
   ```bash
   cd procmine-segmentation
   git pull /path/to/stage1-annotate.bundle main
   ```
   This is a normal fast-forward merge — the bundle behaves like a remote.

3. **Push to GitHub as usual:**
   ```bash
   git push origin main
   git push origin --tags
   ```

4. **Verify:**
   ```bash
   git log --oneline --decorate
   ```
   You should see the new stage's commits and its tag on top of your existing
   history — and GitHub will show a new push event for this stage.

## Notes

- If `git` complains about identity on a new commit later, set it once:
  ```bash
  git config user.name  "Your Name"
  git config user.email "you@example.com"
  ```
  (Existing commits already have an author; this only affects *new* commits you
  make yourself, e.g. if you edit something.)

- The raw datasets are intentionally **not** included (they're git-ignored and
  are large). Only a small slimmed sample session is committed, under
  `data/sample_a/`, so the tests run. Keep your full `dataset_a/` and
  `dataset_b/` locally under `data/` — git will ignore them automatically.

- To run the tests after unzipping or pulling:
  ```bash
  PYTHONPATH=src python3 tests/run_tests.py
  ```

- To see the story of the work at any point:
  ```bash
  git log --oneline --decorate --graph
  git tag -l -n1     # one-line description per stage tag
  ```

