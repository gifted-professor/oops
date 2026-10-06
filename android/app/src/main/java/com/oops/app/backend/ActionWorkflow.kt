package com.oops.app.backend

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay

/** Submission and reconciliation share the same immutable request and operation id. */
object ActionWorkflow {
    /** Only a definite failure allows an explicit re-submit; unknown outcomes must be queried. */
    fun shouldSubmit(previous: ActionResult?) = previous == null || previous.status == ActionResult.FAILED

    suspend fun execute(
        api: OopsApi,
        request: ConfirmRequest,
        submit: Boolean,
        maxPolls: Int = 10,
    ): ActionResult {
        fun checked(r: ActionResult): ActionResult {
            require(r.opId == request.opId) { "后端返回的操作编号不一致" }
            require(r.status in setOf(ActionResult.PENDING, ActionResult.CREATED, ActionResult.FAILED)) {
                "后端返回了未知状态"
            }
            if (r.status == ActionResult.CREATED) require(!r.taskGuid.isNullOrBlank()) { "后端缺少任务编号" }
            return r
        }
        return try {
            var result = checked(if (submit) api.confirmAction(request) else api.getAction(request.opId))
            repeat(maxPolls) {
                if (result.status == ActionResult.PENDING) {
                    delay(1_000)
                    result = checked(api.getAction(request.opId))
                }
            }
            if (result.status == ActionResult.CREATED) {
                val readBack = try {
                    checked(api.getAction(request.opId)).takeIf {
                        it.status == ActionResult.CREATED && it.taskGuid == result.taskGuid
                    }
                } catch (e: CancellationException) { throw e } catch (_: Exception) { null }
                (readBack ?: result).copy(readBackConfirmed = readBack != null, polling = false)
            } else if (result.status == ActionResult.PENDING) {
                result.copy(message = "后端仍在处理，请继续查询结果；不会重复提交", polling = false)
            } else result.copy(polling = false)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // A lost HTTP response does not prove that the write failed.
            ActionResult(request.opId, ActionResult.PENDING,
                message = "结果尚未确认：${e.message ?: e.javaClass.simpleName}。请查询结果", polling = false)
        }
    }
}
