# DxEngine Diagnostic Protocol

## Reasoning Framework

DxEngine uses a hybrid reasoning approach combining:

1. **Pattern Recognition** — Match lab patterns to known disease signatures
2. **Hypothetico-Deductive** — Generate, test, and refine hypotheses
3. **Bayesian Updating** — Quantitative probability updates with likelihood ratios
4. **Adversarial Review** — Systematic bias checking

## Cognitive Bias Checklist

Before convergence, verify:
- [ ] **Anchoring**: Is the top diagnosis the first one considered? If so, actively challenge it
- [ ] **Premature Closure**: Are there unexplained findings being ignored?
- [ ] **Confirmation Bias**: Is evidence being selectively interpreted?
- [ ] **Availability Bias**: Is a common diagnosis being favored simply because it's common?
- [ ] **Framing Effect**: Would the differential change if the data were presented differently?

## Lab Interpretation Rules

1. Always use age/sex-adjusted reference ranges
2. A "normal" value in the wrong clinical context IS a finding (e.g., "normal" TSH in thyroid storm)
3. Trends matter more than single values
4. Consider the collectively-abnormal pattern: individual values may be normal, but the combination may be pathological
5. Lab ratios (BUN/Cr, AST/ALT) provide additional discriminating information

## Hypothesis Management

- Maintain 5-15 hypotheses at all times
- Never drop a hypothesis below 0.1% without explicit evidence ruling it out
- "Can't miss" diagnoses have a probability floor of 5%
- Add new hypotheses whenever orphan findings are identified
- Remove hypotheses only when evidence AGAINST exceeds evidence FOR by 10x

## Convergence Criteria

Converge when ALL of:
1. Top hypothesis unchanged for 2+ iterations
2. Top hypothesis probability > 85%
3. No unexplained critical findings
4. Adversarial agent has not blocked convergence

OR when max iterations (5) reached — output with low confidence flag.
