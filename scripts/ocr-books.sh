#!/bin/sh
# OCR scanned PDFs page by page: books/ocr/<name>/p<N>.txt
set -e
cd "$(dirname "$0")/../books"
for f in "src/Go Books/Attack_Defense/Mastering Invasions.pdf" "src/Go Books/Fundamentals/Elementary Go Series Vol. 7 - Handicap-go.pdf"; do
  name=$(basename "$f" .pdf); out="ocr/$name"; mkdir -p "$out"
  pages=$(pdfinfo "$f" | awk '/^Pages/{print $2}')
  for p in $(seq 1 "$pages"); do
    [ -s "$out/p$p.txt" ] && continue
    pdftoppm -f "$p" -l "$p" -r 300 -gray -png "$f" "/tmp/ocr_page"
    tesseract /tmp/ocr_page-*.png "$out/p$p" -l eng --psm 3 >/dev/null 2>&1 || true
    rm -f /tmp/ocr_page-*.png
  done
  echo "done $name $pages pages"
done
