"""Manually runnable agent-execution recovery scan (MVP 7 branch 3) -- the
same logic the Celery beat periodic task runs, callable directly with no
broker/worker required.
"""

from django.core.management.base import BaseCommand

from agents.tasks import recover_stale_agents


class Command(BaseCommand):
    help = "Reclaim worker-lost AgentSteps and re-dispatch stale-running Agents."

    def handle(self, *args, **options):
        result = recover_stale_agents()
        self.stdout.write(
            self.style.SUCCESS(
                f"Reclaimed {result['reclaimed_steps']} stale step(s), "
                f"re-dispatched {result['redispatched_agents']} stale agent(s)."
            )
        )
