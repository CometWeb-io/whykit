# Automation

Inbound, outbound, nurture, routing and system workflows.

Every workflow gets a specification from `templates/automation-spec-template.md`
before it is built, and the specification names an owner for exceptions. An
automation whose failure path has no owner will fail silently, which is worse
than not having it.

Scripts and tooling may live here. Their secrets never do.
