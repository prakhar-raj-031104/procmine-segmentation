# How to put this repository on GitHub

This bundle is a **complete git repository** — it already contains the full commit
history in its `.git` folder. You are not starting from scratch; you are just
uploading an existing repo to GitHub.

## Steps (run on your own computer)

1. **Unzip** `procmine-segmentation.zip`. You'll get a `procmine-segmentation/` folder.

2. **Open a terminal inside that folder:**
   ```bash
   cd procmine-segmentation
   git log --oneline        # confirm the commit history is present
   ```

3. **Create an empty repository on GitHub**
   - Go to github.com → New repository
   - Name it (e.g. `procmine-segmentation`)
   - **Do NOT** add a README, .gitignore, or license (the repo already has these;
     adding them creates a conflict)
   - Click Create

4. **Connect and push:**
   ```bash
   git remote add origin https://github.com/<your-username>/procmine-segmentation.git
   git branch -M main
   git push -u origin main
   ```

5. Refresh the GitHub page — you'll see all files and the full commit history.

## Notes

- If `git` complains about identity on a new commit later, set it once:
  ```bash
  git config user.name  "Your Name"
  git config user.email "you@example.com"
  ```
  (The existing commits already have an author; this only affects *new* commits
  you make.)

- The raw datasets are intentionally **not** included (they're git-ignored and are
  large). Only a small slimmed sample session is committed, under
  `data/sample_a/`, so the tests run. Keep your full `dataset_a/` and
  `dataset_b/` locally under `data/` — they'll be ignored by git automatically.

- To run the tests after unzipping:
  ```bash
  PYTHONPATH=src python3 tests/run_tests.py
  ```
