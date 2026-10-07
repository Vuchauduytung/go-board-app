# KataGo joseki tree

`tree.json` is built by `python -m joseki build` (see `joseki.py`) on the server, with KataGo searching every
position, and copied here so every deployment has it. The server's own copy (`JOSEKI_PATH`, default
`/data/joseki/tree.json`) wins when there is one.
