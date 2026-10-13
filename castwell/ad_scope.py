"""User-selected removal scope, shared by all contextual classifiers."""

REMOVAL_SCOPE = "sponsor-product-reads-only-v1"

INSTRUCTIONS = """
Removal scope: sponsor and product reads only.
Preserve inserted promos and trailers for other free podcasts, their story premises,
cast descriptions, release schedules, and invitations to listen or follow for free.
Insertion between ads, professional delivery, or a URL alone does not make a free
podcast promo commercial under this policy.
Actual sponsor, product, affiliate, paid-service and paid-subscription offers remain
commercial, including the host's own paid offer, persuasive setup, jokes and terms.
A read selling a paid subscription is a product offer, not a protected free promo.
Keep free program previews and neutral publisher credits separate from adjacent
sponsor speech. Split neighboring sponsor and free-promo units; if one indivisible
unit contains both roles, mark mixed or uncertain and require review.
Unpaid parody and independent product discussion remain protected. Do not invent
payment or an actual sales relationship when the supplied context does not establish it.
"""
