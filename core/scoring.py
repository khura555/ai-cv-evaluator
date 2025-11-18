def computeCvMatchRate(cvScores):
    """
    cvScores: dict with numeric values for keys:
      technical_skills, experience_level, achievements, cultural_fit
    Returns: float 0.0..1.0
    """
    weights = {
        'technical_skills': 0.4,
        'experience_level': 0.25,
        'achievements': 0.2,
        'cultural_fit': 0.15
    }
    # ensure defaults (1 = lowest)
    t = cvScores.get('technical_skills', 1)
    e = cvScores.get('experience_level', 1)
    a = cvScores.get('achievements', 1)
    c = cvScores.get('cultural_fit', 1)
    weighted = (t * weights['technical_skills'] +
                e * weights['experience_level'] +
                a * weights['achievements'] +
                c * weights['cultural_fit'])
    # map 1..5 -> 0..1
    cv_match_rate = (weighted - 1) / 4.0
    if cv_match_rate < 0.0:
        cv_match_rate = 0.0
    if cv_match_rate > 1.0:
        cv_match_rate = 1.0
    return round(cv_match_rate, 3)

def computeProjectScore(projectScores):
    """
    projectScores: dict with correctness, code_quality, resilience, documentation, creativity (1..5)
    Returns weighted average (1..5) rounded to 2 decimals.
    """
    weights = {
        'correctness': 0.3,
        'code_quality': 0.25,
        'resilience': 0.2,
        'documentation': 0.15,
        'creativity': 0.1
    }
    c = projectScores.get('correctness', 1)
    cq = projectScores.get('code_quality', 1)
    r = projectScores.get('resilience', 1)
    d = projectScores.get('documentation', 1)
    cr = projectScores.get('creativity', 1)
    weighted = (c * weights['correctness'] +
                cq * weights['code_quality'] +
                r * weights['resilience'] +
                d * weights['documentation'] +
                cr * weights['creativity'])
    return round(weighted, 2)
