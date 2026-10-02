# Go books indexed for the chat assistant (files under books/src/Go Books/).
# kind: pdf = text layer, ocr = scanned PDF read from books/ocr/<file stem>/p<N>.txt (scripts/ocr-books.sh), epub = chapters.
# Skipped: Joseki/Ai围棋定式大全(重排本).pdf — 3,574 pages of diagrams with ~140 characters of text each.

BOOKS = [
    dict(id='basic-corner-shapes', title='Basic Corner Shapes', topic='Sống chết', lang='en', kind='pdf',
         file='Life_and_Death/317525652-Basic-Corner-Shapes.pdf'),
    dict(id='get-strong-invading', title='Get Strong at Invading', topic='Tấn công & phòng thủ', lang='en', kind='pdf',
         file='Attack_Defense/721197281-get-strong-at-invading-4871870553-9784871870559-compress.pdf'),
    dict(id='egs5-attack-defense', title='Elementary Go Series Vol. 5 – Attack and Defense', topic='Tấn công & phòng thủ',
         lang='en', kind='pdf', file='Attack_Defense/Elementary Go Series Vol. 5 - Attack-and-defense.pdf'),
    dict(id='mastering-invasions', title='Mastering Invasions', topic='Tấn công & phòng thủ', lang='en', kind='ocr',
         file='Attack_Defense/Mastering Invasions.pdf'),
    dict(id='shape-up', title='Shape Up!', topic='Cơ bản', lang='en', kind='pdf',
         file='Fundamentals/289778399-shape-up.pdf'),
    dict(id='egs7-handicap-go', title='Elementary Go Series Vol. 7 – Handicap Go', topic='Cơ bản', lang='en', kind='ocr',
         file='Fundamentals/Elementary Go Series Vol. 7 - Handicap-go.pdf'),
    dict(id='encyclopedia-principles', title='An Encyclopedia of Go Principles (sample)', topic='Cơ bản', lang='en',
         kind='epub', file='Fundamentals/sg0107_ki_k79_sample.epub'),
    dict(id='essential-proverbs', title='Essential Go Proverbs', topic='Cơ bản', lang='en', kind='epub',
         file='Fundamentals/sg0171_ki_k20_book.epub'),
    dict(id='joseki-revolution-2', title='Joseki Revolution 2', topic='Định thức', lang='en', kind='epub',
         file='Joseki/sg0169_ki_k90_book JOSEKI RENOVATION.epub'),
    dict(id='ly-thuyet-khai-cuoc', title='Lý thuyết khai cuộc thật đơn giản', topic='Khai cuộc', lang='vi', kind='pdf',
         file='Opening/S02LyThuyetKhaiCuocThatDonGian.pdf'),
    dict(id='tu-dien-khai-cuoc', title='Từ điển khai cuộc', topic='Khai cuộc', lang='vi', kind='pdf',
         file='Opening/tu dien khai cuoc.pdf'),
]

BY_ID = {b['id']: b for b in BOOKS}
