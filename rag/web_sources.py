# Web pages on joseki in the AI era indexed for the assistant next to the books (rag.ingest_web), cited with their
# link. kind: html = the page's text, wikipedia = the article's plain text (MediaWiki API), discourse = every post of a
# forum topic (Discourse /raw/<topic id>). Not all of them are openly licensed: they are searched privately and only
# short excerpts are shown, with the link to the original.

TOPIC = 'Định thức (AI)'
_RIS = 'https://resigning-in-sente.github.io/joseki/'

PAGES = [
    # KataGo studies of modern joseki, by "resigning in sente" (2021)
    dict(id='ris-44-low-slide-attach', title='4-4 Low Approach – Slide or Attach? (KataGo)', lang='en', kind='html',
         url=_RIS + '44/2021/02/18/44-low-slide-attach/'),
    dict(id='ris-33-invasion-1', title='The 3-3 Invasion 1 – Introduction (KataGo)', lang='en', kind='html',
         url=_RIS + '44/2021/02/21/44-33-invasion-1-intro/'),
    dict(id='ris-33-invasion-2-1', title='The 3-3 Invasion 2-1 – The Double Hane (KataGo)', lang='en', kind='html',
         url=_RIS + '44/2021/02/21/44-33-invasion-2-1-double-hane/'),
    dict(id='ris-33-invasion-2-2', title='The 3-3 Invasion 2-2 – The 3-4 Invasion (KataGo)', lang='en', kind='html',
         url=_RIS + '44/2021/02/21/44-33-invasion-2-2-34-invasion/'),
    dict(id='ris-33-invasion-3', title="The 3-3 Invasion 3 – The Knight's Move vs. the Push (KataGo)", lang='en',
         kind='html', url=_RIS + '44/2021/07/21/44-33-invasion-3-knight-vs-push/'),
    dict(id='ris-34-pincer-avoid', title='3-4 Point – Avoiding the Pincer and Tenuki (KataGo)', lang='en', kind='html',
         url=_RIS + '34/2021/07/09/34-pincer-avoid/'),
    dict(id='ris-34-attach-drawback', title='3-4 Point – The Attachment and its Drawback (KataGo)', lang='en',
         kind='html', url=_RIS + '34/2021/07/20/34-attach-drawback-1-intro/'),
    dict(id='ris-34-44-attach', title='3-4 Approaches – the 4-4 Attachment (KataGo)', lang='en', kind='html',
         url=_RIS + '34/2021/08/03/44-attach/'),
    # background: what AI changed
    dict(id='wiki-joseki', title='Jōseki (Wikipedia)', lang='en', kind='wikipedia', url='https://en.wikipedia.org/wiki/J%C5%8Dseki'),
    dict(id='wiki-taisha', title='Taisha jōseki (Wikipedia)', lang='en', kind='wikipedia',
         url='https://en.wikipedia.org/wiki/Taisha_j%C5%8Dseki'),
    dict(id='deepmind-innovations', title='Innovations of AlphaGo (DeepMind)', lang='en', kind='html',
         url='https://deepmind.google/blog/innovations-of-alphago/'),
    dict(id='aimastergo-katago-history', title='KataGo and the History of AI Go (AI Master Go)', lang='en', kind='html',
         url='https://www.aimastergo.com/en/learn/katago-and-ai-go/'),
    dict(id='imotaro-ai-study', title='Học khai cuộc và định thức bằng AI – kỳ thủ 9-dan Fox (芋太郎)', lang='ja', kind='html',
         url='https://note.com/imotaro_15/n/n5477fbc42623'),
    dict(id='ogs-alphago-joseki', title='AlphaGo joseki (Zero and Master) – diễn đàn OGS', lang='en', kind='discourse',
         url='https://forums.online-go.com/t/alphago-joseki-zero-and-master/14332'),
    dict(id='ogs-ai-taught-new', title='Has AI taught us anything new recently? – diễn đàn OGS', lang='en',
         kind='discourse', url='https://forums.online-go.com/t/has-ai-taught-us-anything-new-recently/23399'),
    dict(id='ogs-33-variation', title='3-3 invasion variation – diễn đàn OGS', lang='en', kind='discourse',
         url='https://forums.online-go.com/t/3-3-invasion-variation/56508'),
    dict(id='ogs-33-response', title='3-3 invasion response – diễn đàn OGS', lang='en', kind='discourse',
         url='https://forums.online-go.com/t/3-3-invasion-response/32523'),
]

BY_ID = {p['id']: p for p in PAGES}
