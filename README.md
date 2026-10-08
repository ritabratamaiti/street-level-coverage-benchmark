# Street-level coverage benchmark

How much of the world's road network has street-level imagery? This repository measures it
with random points on mapped roads, each checked for official Google Street View imagery.
The points, the results and the code are all here, so anyone can check the number or
extend it.

## Result

Of 120 random points on mapped roads, **87 (72.5%) had no official Google Street View
imagery within about 120 m**. The 95% interval is 64% to 80%. Checked on 19 September 2026.

| Continent | Points | With official coverage | Without |
|---|---:|---:|---:|
| Africa | 31 | 0 | 100% |
| Asia | 56 | 13 | 77% |
| South America | 11 | 7 | 36% |
| North America | 10 | 5 | 50% |
| Europe | 8 | 5 | 38% |
| Oceania | 4 | 3 | 25% |
| **All** | **120** | **33** | **72.5%** |

Each point is assigned to the continent of the country it falls in, with Russia split at
60°E. Continent counts are small, so read them as indicative. `data/summary.json` has a 95%
interval for every row, and a per-country breakdown.

- **Africa: 0 of 31.** None of the sampled African roads had official coverage.
- **Part of the gap is political.** 23 points fall in China and Iran, where Google does not
  collect imagery. Leaving out China alone, 68% of points had no coverage.

`data/points.geojson` renders as a map on GitHub: green points are covered, red are not.

## Method

**Sampling.** Each sample starts with a random window 0.35° across (about 39 km), drawn
uniformly by area from one of eight regional boxes. The box weights roughly follow land
area. The script asks Overpass for up to 40 roads in the window, from motorways down to
service roads, picks one road at random and then a random node on it. Windows with no roads
are redrawn. Every point is therefore on a mapped road, and a sparsely mapped district
counts as much as a dense city. The figure describes road-accessible places, not where
people live.

**Coverage check.** A point is covered if official Google Street View imagery exists within
about 120 m. The script probes the point and four offsets around it, to the north, south,
east and west. One lookup returns only the nearest panorama, and a user upload there can
hide official imagery a few metres away. An earlier version that checked only the nearest
panorama undercounted coverage at landmark locations, which is why the probe grid exists.
"Official" means imagery credited to Google. User photospheres and other uploads do not
count.

**Country labels.** Reverse geocoding with Nominatim, at country level.

**Sampling regions and continents.** Earlier summaries of this run reported results by
sampling box, for example Europe 5 of 6. The boxes are rectangles and do not follow
borders: the Africa box reaches into Arabia, and two points drawn from the Asia box are in
Somalia. The table above therefore uses countries. Both breakdowns are in
`data/summary.json`.

## Limitations

- **One provider.** Only official Google imagery is counted. Mapillary, Panoramax,
  KartaView, Apple Look Around, Baidu, Tencent, Yandex, Naver and Kakao are not. A place
  with no Google coverage may have imagery from one of these. Adding the open providers is
  the next step, and contributions are welcome.
- **Sample size.** 120 points pins the headline to within about 8 percentage points, but
  the per-continent intervals are wide.
- **Coarse sampling regions.** The boxes have hand-set weights and they overlap. The South
  Asia and Southeast Asia boxes also sit inside the Asia box, so those areas are
  over-represented. Dropping every point drawn from the South Asia box moves the headline
  to 74%. A version that samples from a land polygon is planned.
- **Overpass ordering.** Overpass returns the first 40 roads in a window, not a random 40.
- **Failed requests in the published run.** When an Overpass request failed, the published
  run drew a new window, the same as for a window with no roads. Busy servers fail more
  often on dense areas, so cities may be slightly under-sampled. The script now retries the
  same window instead.
- **Probe geometry.** Offsets are 120 / 111,320 degrees on both axes, so the east and west
  probes sit slightly further out away from the equator. This is kept as it was so that
  re-checks match the published run.
- **A snapshot.** Coverage changes over time. The published run is from 19 September 2026.

## Reproduce

```bash
pip install -r requirements.txt

# Summarise the published run
python coverage_benchmark.py report --points data/points.csv --by-country

# Re-check the published points against today's coverage
export GOOGLE_MAPS_API_KEY=...   # metadata requests are not billed
python coverage_benchmark.py check --points data/points.csv --out data/recheck.csv
python coverage_benchmark.py report --points data/recheck.csv

# Draw and check a fresh, larger sample
python coverage_benchmark.py sample --n 500 --seed 7 --out data/sample_500.csv
python coverage_benchmark.py label --points data/sample_500.csv
python coverage_benchmark.py check --points data/sample_500.csv
python coverage_benchmark.py report --points data/sample_500.csv
```

`check` uses the Street View Static API metadata endpoint. It stops on an invalid key or a
refused request instead of recording those points as uncovered. `sample` retries a failed
Overpass request on the same window, and `--overpass-url` points it at a mirror when the
main server is busy.

OpenStreetMap changes every day, so `sample --seed 42` will not redraw the same 120 points.
That is why the points themselves are published.

## Data

| File | Contents |
|---|---|
| `data/points.csv` | The 120 points: coordinates, road class, sampling region, country, continent, result |
| `data/points.geojson` | The same points as a map |
| `data/summary.json` | Totals and 95% intervals by continent, sampling region and country |

## Licence

The code is MIT licensed. The points are OpenStreetMap road nodes and are available under
the [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/),
© OpenStreetMap contributors. Country labels also come from OpenStreetMap, via Nominatim.

## Citation

Maiti, R. (2026). *Street-level coverage benchmark.*
https://github.com/ritabratamaiti/street-level-coverage-benchmark

## Contact

Ritabrata Maiti, [orisynth.dev](https://orisynth.dev), hello@orisynth.dev. Corrections and
pull requests are welcome, especially from people who build or maintain street-level
imagery.
