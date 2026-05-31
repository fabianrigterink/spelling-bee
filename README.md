# spelling-bee

A repository to analyze New York Times (NYT) Spelling Bee puzzles.

## First time here?

Create and activate the virtual environment - and install the dependencies - by running:
```sh
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Want to scrape the NYT's historical Spelling Bee puzzles?

Scrape the NYT's historical Spelling Bee puzzles (from __[sbsolver.com](https://www.sbsolver.com/)__) by running:
```sh
python scrape_nyt_spelling_bee_puzzles.py --out nyt_spelling_bee_puzzles.csv
```

## Want to re-run the analysis or expand it?

Execute the notebook, `analyze_spelling_bee_puzzles.ipynb`.
