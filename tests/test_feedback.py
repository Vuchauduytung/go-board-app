import pytest

import feedback
import telegram_bot


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, 'ROOT', tmp_path)


def test_anonymous_review_hides_author_publicly_only():
    r = feedback.add('an@gmail.com', 4, 'Hay lắm', anonymous=True)
    feedback.add('binh@gmail.com', 2, 'Hơi chậm', anonymous=False)
    pub = feedback.list_public('an@gmail.com')
    assert pub['count'] == 2 and pub['average'] == 3.0
    mine = next(x for x in pub['reviews'] if x['id'] == r['id'])
    assert mine['author'] == 'Ẩn danh' and mine['mine'] and 'an@gmail.com' not in str(pub)
    assert 'binh' in str(pub)
    assert feedback.all_reviews()[1]['user'] == 'an@gmail.com'   # admins still see who wrote it
    assert 'an@gmail.com (ẩn danh công khai)' in telegram_bot.describe(r)


def test_daily_limit_and_delete_own_only(monkeypatch):
    monkeypatch.setattr(feedback, 'DAILY_PER_USER', 2)
    a = feedback.add('a@x.com', 5, '1', False)
    feedback.add('a@x.com', 5, '2', False)
    with pytest.raises(feedback.TooMany):
        feedback.add('a@x.com', 5, '3', False)
    assert not feedback.delete(a['id'], 'b@x.com')
    assert feedback.delete(a['id'], 'a@x.com')
    assert not feedback.delete('../../etc/passwd')


def test_bot_commands(monkeypatch):
    r = feedback.add('a@x.com', 1, 'Nhận dạng sai quân', False)
    assert 'Nhận dạng sai quân' in telegram_bot.handle(1, '/reviews')
    assert '1 góp ý' in telegram_bot.handle(1, '/stats')
    assert 'Đã ẩn' in telegram_bot.handle(1, f'/hide {r["id"][:8]}')
    assert feedback.list_public(None)['count'] == 0 and feedback.get(r['id'])['hidden']
    assert 'hiện lại' in telegram_bot.handle(1, f'/show {r["id"][:6]}')
    asked = {}
    monkeypatch.setattr(telegram_bot, '_ask_llm', lambda q, items: asked.setdefault('n', len(items)) and 'tóm tắt')
    assert telegram_bot.handle(1, '/summary 7') == 'tóm tắt' and asked['n'] == 1
    assert 'Không tìm thấy' in telegram_bot.handle(1, '/delete ab')


def test_webhook_needs_secret_and_admin(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(telegram_bot, 'TOKEN', 't')
    monkeypatch.setattr(telegram_bot, 'SECRET', 's3cret')
    monkeypatch.setattr(telegram_bot, 'ADMIN_IDS', {42})
    sent = []
    monkeypatch.setattr(telegram_bot, 'send', lambda chat, text: sent.append((chat, text)))
    c = TestClient(telegram_bot.app)
    msg = lambda uid, chat, text: {'update_id': uid, 'message': {'chat': {'id': chat}, 'text': text}}
    assert c.post('/telegram/webhook', json=msg(1, 42, '/help')).status_code == 404
    assert c.post('/telegram/webhook', json=msg(2, 42, '/help'),
                  headers={'X-Telegram-Bot-Api-Secret-Token': 'wrong'}).status_code == 404
    h = {'X-Telegram-Bot-Api-Secret-Token': 's3cret'}
    c.post('/telegram/webhook', json=msg(3, 7, '/reviews'), headers=h)       # not an admin: nothing
    c.post('/telegram/webhook', json=msg(4, 7, '/start'), headers=h)         # ...except their chat id
    c.post('/telegram/webhook', json=msg(5, 42, '/help'), headers=h)
    c.post('/telegram/webhook', json=msg(5, 42, '/help'), headers=h)         # Telegram retry: once
    assert [chat for chat, _ in sent] == [7, 42] and 'Chat id của bạn: 7' in sent[0][1]


def _photo(exif=True):
    import io
    from PIL import Image
    img, out = Image.new('RGB', (3000, 1000), 'red'), io.BytesIO()
    ex = Image.Exif()
    if exif:
        ex[0x8825] = {2: (21.0, 1.0, 0.0), 1: 'N'}   # GPSInfo
        ex[0x0112] = 6                                # rotated 90°: stored sideways
    img.save(out, 'JPEG', exif=ex.tobytes())
    return out.getvalue()


def test_photos_lose_metadata_and_go_with_the_review():
    from PIL import Image
    jpeg = feedback.encode_image(_photo())
    img = Image.open(__import__('io').BytesIO(jpeg))
    assert not img.getexif() and b'Exif' not in jpeg[:200]
    assert img.size == (533, 1600)   # rotated upright by its EXIF, then at most 1600 px
    with pytest.raises(feedback.BadImage):
        feedback.encode_image(b'not an image')
    r = feedback.add('a@x.com', None, '', False, [jpeg, jpeg])
    files = feedback.image_paths(r)
    assert r['images'] == 2 and all(f.is_file() for f in files)
    assert feedback.list_public(None)['reviews'][0]['images'] == 2
    assert '📷 2 ảnh' in telegram_bot.describe(r)
    assert feedback.image_path(r['id'], 5) is None and feedback.image_path('../x', 0) is None
    feedback.delete(r['id'])
    assert not any(f.exists() for f in files)
