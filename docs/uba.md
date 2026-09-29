# User Business Access (UBA)

UBA answers "this user acts on that business object". It replaces the dedicated
associations — claim admin → health facility, `OfficerVillage`, ... — with one table, and
it decides two different things that are deliberately kept apart:

| Question | Answered by |
| --- | --- |
| *May this user exercise that right on that object?* | `User.has_perms(right, access_requirements=...)` |
| *Which rows may this user see?* | `Model.get_queryset(queryset, user)` |

A link never carries a right. `UserBusinessAccess.link_type` is the **credential** the user
acts under on the object; whether a right follows is answered by the role's UBA bag
(`RoleRight.uba = True`). So revoking a role strips the right immediately, and attaching an
object never pulls a role onto a user.

## The registry (`core/uba_link_types.py`)

A credential is a registered code, not a foreign key to a role: roles get renamed,
recreated and duplicated by implementers, a code does not. **The module that owns the model
registers the credential**, in its `AppConfig.ready()`:

```python
register_uba_link_type(
    ENROLMENT_UBA_LINK_TYPE,
    "Enrolment officer of the village",
    models=(VILLAGE_MODEL,),
    params={"location_type": "V"},
)
```

`CLAIM_ADMIN` and `ENROLMENT` are therefore declared by **location**, which owns the health
facility and the village. Core only keeps their codes (`core.apps`), so that a module can
name a credential without depending on location.

`params` is an open bag. Unknown keys are ignored, so a module may put its own things in
there; the keys the location row filter reads are listed in `LOCATION_PARAMS`:

| Key | Meaning |
| --- | --- |
| `location_type` | the loc_type the linked Location sits at, `'V'` for a village credential. A code, not a depth, because `LocationConfig.location_types` is deployment configuration |
| `location_field` | for a credential held on a model hanging off a location (a health facility), the field pointing at the Location |
| `include_descendants` | a link also covers what sits below the linked location, so a link granted on a district reaches its villages |
| `include_ancestors` | ... and what sits above |

The registry drives the model validation, the django admin picker, the `ubaLinkTypes`
GraphQL query (`code`, `label`, `models`, `params`) and the row filtering below.

## Row filtering

**Put the rule in `Model.get_queryset(queryset, user)`, never in a resolver.** It is the
single entry point GraphQL and the REST/FHIR API share — the latter calls
`Model.get_queryset(None, request.user)` — which is why a `DjangoObjectType` must route
through it rather than restate a filter:

```python
@classmethod
def get_queryset(cls, queryset, info):
    return Policy.get_queryset(queryset, info)
```

`LocationManager.build_user_location_filter_query` applies the narrowing, and the module
says **which credential governs its rows** through `link_types`:

```python
queryset = LocationManager().build_user_location_filter_query(
    user._u, prefix='health_facility__location', queryset=queryset, loc_types=['D'],
    link_types=CLAIM_ADMIN_UBA_LINK_TYPE)
```

Given `prefix` (a path to a Location), `loc_types` (the type that path points at) and
`link_types`, it:

1. builds the usual district filter from `tblUsersDistricts` (`allowed`);
2. builds one term per **demanded** credential the user actually holds a link on, and
   **OR**s them — holding an `ENROLMENT` link and a `CLAIM_ADMIN` link widens what the user
   sees, it does not intersect it;
3. **AND**s the two. A credential narrows *within* the districts the user is assigned to;
   it never reaches outside them.

A user holding no demanded link keeps the plain district filter: the UBA half answers
`None` rather than an empty filter, so an absent credential does not empty the queryset.

### `link_types` is opt-in

`link_types` accepts one code, a list of codes, or `"ALL"` (`ALL_UBA_LINK_TYPES`, also
honoured inside a list) for every location aware credential. **It defaults to `None`, i.e.
no UBA narrowing at all.**

That default is the point: a credential must not narrow a scope it says nothing about. If
every credential applied everywhere, a clerk who is claim admin of one facility would see
their *enrolment* scope collapse onto it. Only the module owning the rows knows what
governs them, so each `get_queryset` names its own:

| Module | Rows | Demands |
| --- | --- | --- |
| `claim` | claim, attachment, feedback, feedback prompt, claim admin | `CLAIM_ADMIN` |
| `insuree` | family, insuree, insuree policy | `ENROLMENT` |
| `policy` | policy, through `family__location` | `ENROLMENT` |
| `location` | locations, the generic health facility list | *(none: the district filter alone)* |

The generic health facility list is deliberately left alone: being claim admin of one
facility must not hide the other facilities of the user's districts from every other
screen. The claim module demands `CLAIM_ADMIN` on the facility lists *it* builds.

### How a path is worked out

This is what `params` buys. The path a module passes points at a location, rarely at the
object the credential is held on, so the filter re-aims it:

| Credential | Path the module passes | What the filter uses |
| --- | --- | --- |
| `CLAIM_ADMIN` (`location_field='location'`) | `health_facility__location` | `health_facility` |
| `ENROLMENT` (`location_type='V'`, path at `'D'`) | `location__parent__parent` | `location` |
| `ENROLMENT` | `insuree__family__location__parent__parent` | `insuree__family__location` |

A location path is built by climbing from the row's own location, so going back down is
dropping trailing `__parent` segments (`_walk_prefix`), and going up is adding them. When a
path cannot be walked down that far, the filter compares what the row *does* expose — its
ancestor at the path's level — so the narrowing still applies, coarser, instead of being
silently dropped.

## The other half: the permission gates

`has_perms` only consults the UBA bag when the caller hands it a business map:

```python
user.has_perms(ClaimConfig.gql_mutation_update_claims_perms,
               access_requirements=['location.healthfacility', hf_uuid, 'CLAIM_ADMIN'])
```

The model label may be `None` — the registry already knows which models a credential is
declared on — and the credential may be left out, in which case any credential on that
instance satisfies the map.

Note that no module passes `access_requirements` yet. Until they do, a right that only
sits in a role's UBA bag is granted nowhere, and the credential shows up purely as the row
narrowing above.

## Tests

- `core/tests/test_uba.py` — `RoleRight.uba` and the UBA branch of `has_perms`
- `core/tests/test_uba_filters.py` — the id resolution and the prefix helper
- `location/test_uba.py` — the registration, the path derivation, the OR/AND composition
- `claim/tests/test_uba.py` — every claim right, on the linked health facility only
- `insuree/tests/test_uba.py`, `policy/test_uba.py` — the `ENROLMENT` narrowing
