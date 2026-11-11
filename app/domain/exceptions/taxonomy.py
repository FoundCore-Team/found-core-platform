from app.domain.enums import TagMergeTargetProblem, TagStatus


class CategoryNotFoundError(Exception):
    message = "Category not found."

    def __init__(self) -> None:
        super().__init__(self.message)


class RoleNotFoundError(Exception):
    message = "Role not found."

    def __init__(self) -> None:
        super().__init__(self.message)


class TagTitleInvalidError(Exception):
    message = "Tag title is invalid."

    def __init__(self) -> None:
        super().__init__(self.message)


class TagRejectedError(Exception):
    message = "This tag was rejected by moderation."

    def __init__(self) -> None:
        super().__init__(self.message)


class TagNotFoundError(Exception):
    message = "Tag not found."

    def __init__(self) -> None:
        super().__init__(self.message)


class TagStatusConflictError(Exception):
    message = "The tag status does not allow this action."

    def __init__(self, status: TagStatus) -> None:
        super().__init__(self.message)
        self.status = status


class TagMergeTargetInvalidError(Exception):
    message = "The merge target is invalid."

    def __init__(self, problem: TagMergeTargetProblem) -> None:
        super().__init__(self.message)
        self.problem = problem
