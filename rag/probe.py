from rag.core import search
QS = ['Song phi yến là gì và nên ứng phó thế nào?',
      'Khi nào nên xâm nhập vào khung thế của đối phương?',
      'What does the proverb "play away from thickness" mean?',
      'Tam giác rỗng là hình xấu vì sao?',
      'Chấp 9 quân thì Đen nên chơi như thế nào?',
      'How should White invade at the 3-3 point under a star point?',
      'Làm sao để quân trong góc sống được (sống chết góc)?',
      'Thang (ladder) hoạt động thế nào?']
for q in QS:
    print('\n#', q)
    for c in search(q, top_k=5, min_score=0.0):
        print(f'  {c.score:.3f} {c.book_id:24s} {c.page_kind[0]}{c.page:<4d} {c.text[:90]!r}')
