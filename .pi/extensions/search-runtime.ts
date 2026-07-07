import type { ExtensionAPI, ExtensionContext, ToolCallEvent } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const role = process.env.AGENTIC_ANY_SEARCH_PI_ROLE || "main";
const runtimeRoot = process.env.AGENTIC_ANY_SEARCH_ROOT || ".search";
let workspaceRoot: string | undefined;
let sawContext = false;

const JsonArgs = Type.Object({}, { additionalProperties: true });

async function runJsonCli(pi: ExtensionAPI, _ctx: ExtensionContext, tool: string, args: Record<string, unknown>) {
	const result = await pi.exec("agentic-any-search-pi-tool", [
		"--root",
		runtimeRoot,
		"--args-json",
		JSON.stringify(args),
		tool,
	]);
	if (result.code !== 0) {
		return {
			content: [{ type: "text" as const, text: result.stderr || result.stdout || `${tool} failed` }],
			details: { tool, ok: false },
		};
	}
	const parsed = JSON.parse(result.stdout || "null");
	return {
		content: [{ type: "text" as const, text: JSON.stringify(parsed, null, 2) }],
		details: parsed,
	};
}

function registerRuntimeTool(pi: ExtensionAPI, name: string) {
	pi.registerTool({
		name,
		label: name,
		description: `Call search-runtime facade tool ${name}.`,
		parameters: JsonArgs,
		executionMode: "sequential",
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			const result = await runJsonCli(pi, ctx, name, params as Record<string, unknown>);
			if (name === "search_get_agent_context") {
				const details = result.details as { workspace?: string } | undefined;
				workspaceRoot = details?.workspace;
				sawContext = true;
			}
			return result;
		},
	});
}

function registerPiWorkerTool(pi: ExtensionAPI) {
	pi.registerTool({
		name: "pi_rpc_run_worker",
		label: "Pi RPC Worker",
		description: "Run a Pi RPC worker from a search_start_agent_session launch payload. Returns a handle for search_bind_agent_handle.",
		parameters: Type.Object({
			launch: Type.Object({}, { additionalProperties: true }),
		}),
		executionMode: "sequential",
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			const result = await pi.exec("agentic-any-search-pi-worker", [
				"run",
				"--launch-json",
				JSON.stringify(params.launch),
			]);
			if (result.code !== 0) {
				return {
					content: [{ type: "text" as const, text: result.stderr || result.stdout || "pi_rpc_run_worker failed" }],
					details: { ok: false },
				};
			}
			const handle = JSON.parse(result.stdout || "{}");
			return {
				content: [{ type: "text" as const, text: JSON.stringify(handle, null, 2) }],
				details: handle,
			};
		},
	});
}

function extractCandidatePath(event: ToolCallEvent): string | undefined {
	const input = event.input as Record<string, unknown>;
	if (event.toolName === "bash") return String(input.command || "");
	for (const key of ["path", "file_path", "filePath"]) {
		if (typeof input[key] === "string") return input[key] as string;
	}
	return undefined;
}

function workspaceGuard(event: ToolCallEvent) {
	if (role !== "worker") return undefined;
	if (event.toolName === "search_get_agent_context") return undefined;
	if (!sawContext) {
		const readOnly = new Set(["read", "grep", "find", "ls"]);
		if (readOnly.has(event.toolName)) return undefined;
		return { block: true, reason: "Call search_get_agent_context before mutating tools." };
	}
	if (!workspaceRoot) return undefined;
	if (!["edit", "write", "bash"].includes(event.toolName)) return undefined;
	const target = extractCandidatePath(event);
	if (target && target.includes("..")) {
		return { block: true, reason: "workspaceGuard blocked parent-directory path." };
	}
	if (target && target.startsWith("/") && !target.startsWith(workspaceRoot)) {
		return { block: true, reason: "workspaceGuard blocked access outside candidate workspace." };
	}
	return undefined;
}

async function mainGate(event: ToolCallEvent, ctx: ExtensionContext) {
	if (role !== "main") return undefined;
	if (!event.toolName.startsWith("search_")) return undefined;
	const goalPlusId = process.env.AGENTIC_ANY_SEARCH_GOAL_PLUS_ID;
	if (!goalPlusId) return undefined;
	const gate = await runJsonCli(piForGate, ctx, "goal_plus_gate", {
		goal_plus_id: goalPlusId,
		event: "pre_tool_use",
		context: { tool: event.toolName, input: event.input },
	});
	const details = gate.details as { decision?: string; reason?: string } | undefined;
	if (details?.decision === "block") {
		return { block: true, reason: details.reason || "goal_plus_gate blocked search tool use" };
	}
	return undefined;
}

let piForGate: ExtensionAPI;

export default function (pi: ExtensionAPI) {
	piForGate = pi;
	const mainTools = [
		"goal_plus_create",
		"goal_plus_status",
		"goal_plus_record_triage",
		"goal_plus_save_spec_draft",
		"goal_plus_confirm_frozen_verifier",
		"goal_plus_link_search_run",
		"goal_plus_record_search_result",
		"goal_plus_set_status",
		"goal_plus_gate",
		"search_freeze_spec",
		"search_create",
		"search_status",
		"search_list_history",
		"search_plan_next",
		"search_start_batch",
		"search_start_agent_session",
		"search_redispatch_candidate",
		"search_bind_agent_handle",
		"search_continue_agent_session",
		"search_run_verifier",
		"search_select",
		"search_report",
		"search_promote",
	];
	const workerTools = ["search_get_agent_context", "search_run_verifier", "search_list_iterations"];
	for (const tool of role === "worker" ? workerTools : mainTools) {
		registerRuntimeTool(pi, tool);
	}
	if (role === "main") registerPiWorkerTool(pi);
	pi.on("tool_call", async (event, ctx) => {
		return workspaceGuard(event) || (await mainGate(event, ctx));
	});
}
