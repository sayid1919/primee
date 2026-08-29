from primee.core.result_types import SkillResult


def run(context):
    return SkillResult.ok("alpha", "alpha ran", structured_data={"echo": context.inputs})
