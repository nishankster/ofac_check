
import re
import unicodedata


def normalize(s: str) -> str:
    """Lowercase, remove accents/punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return " ".join(s.split())


def jaro_winkler(s1: str, s2: str, p: float = 0.1) -> float:
    """Jaro-Winkler similarity between two strings."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    match_dist = max(len1, len2) // 2 - 1
    match_dist = max(match_dist, 0)

    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = transpositions = 0

    for i in range(len1):
        start = max(0, i - match_dist)
        end   = min(i + match_dist + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    jaro = (matches / len1 + matches / len2 + (matches - transpositions / 2) / matches) / 3

    # Winkler prefix bonus
    prefix = 0
    for i in range(min(4, len1, len2)):
        if s1[i] == s2[i]:
            prefix += 1
        else:
            break
    return jaro + prefix * p * (1 - jaro)


def levenshtein_similarity(s1: str, s2: str) -> float:
    """Normalized Levenshtein similarity: 1 - (edit_distance / max_length).

    Calibrated thresholds for OFAC use: BLOCKED ≥ 0.85, REVIEW ≥ 0.75.
    """
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    prev = list(range(len2 + 1))
    for i, c1 in enumerate(s1, 1):
        curr = [i] + [0] * len2
        for j, c2 in enumerate(s2, 1):
            if c1 == c2:
                curr[j] = prev[j - 1]
            else:
                curr[j] = 1 + min(prev[j], curr[j - 1], prev[j - 1])
        prev = curr

    return 1.0 - prev[len2] / max(len1, len2)


def ngram_similarity(s1: str, s2: str, n: int = 2) -> float:
    """Bigram Dice coefficient over character n-grams.

    Robust to transliterations and spelling variants across languages.
    Calibrated thresholds for OFAC use: BLOCKED ≥ 0.75, REVIEW ≥ 0.65.
    """
    if s1 == s2:
        return 1.0

    def _counts(s: str) -> dict:
        counts: dict[str, int] = {}
        for i in range(len(s) - n + 1):
            g = s[i:i + n]
            counts[g] = counts.get(g, 0) + 1
        return counts

    if len(s1) < n or len(s2) < n:
        # Short-string fallback: character-set overlap (Dice on unigrams)
        chars1 = set(s1)
        chars2 = set(s2)
        denom = len(chars1) + len(chars2)
        return 2 * len(chars1 & chars2) / denom if denom else 0.0

    c1, c2 = _counts(s1), _counts(s2)
    intersection = sum(min(c1.get(g, 0), c2.get(g, 0)) for g in c1)
    total = (len(s1) - n + 1) + (len(s2) - n + 1)
    return 2 * intersection / total


def string_similarity(s1: str, s2: str, algorithm: str = "jaro_winkler") -> float:
    """Dispatch to the requested similarity algorithm.

    Args:
        algorithm: one of "jaro_winkler", "levenshtein", "ngram"
    """
    if algorithm == "levenshtein":
        return levenshtein_similarity(s1, s2)
    if algorithm == "ngram":
        return ngram_similarity(s1, s2)
    return jaro_winkler(s1, s2)
