from westie_agent.vocabulary_words import LEVELS, WORDS, WORD_BY_ID


def test_shared_spelling_vocabulary_remains_complete_and_unique():
    assert len(WORDS)==len(WORD_BY_ID)==90
    assert all(sum(word.level==level for word in WORDS)==30 for level in LEVELS)
    assert all(w.term and w.meaning and w.part and w.example and w.translation for w in WORDS)
