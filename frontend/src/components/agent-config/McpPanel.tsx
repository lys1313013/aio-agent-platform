import { useState } from 'react';
import { ApiOutlined, RightOutlined } from '@ant-design/icons';
import { Checkbox, Tag, Typography } from 'antd';
import { cn } from '@/lib/utils';
import { useAgentConfig } from './context';
import { SaveButtonRow } from './SaveButtonRow';

const { Text } = Typography;

export function McpPanel() {
  const { mcpServers, enabledTools, enabledMcpTools, setEnabledMcpTools, mcpAllToolsServerIds, setMcpAllToolsServerIds, savingSections, savedSections, saveSection } = useAgentConfig();
  const [expandedMcpServerIds, setExpandedMcpServerIds] = useState<string[]>([]);
  return (

    <div className="agent-config-panel">
      <Text type="secondary" className="text-xs block mb-3">
        共 {mcpServers.length} 个 MCP 服务。点击服务名称展开工具，勾选复选框选择工具。
      </Text>

      {mcpServers.length === 0 ? (
        <div className="py-6 text-center">
          <ApiOutlined className="text-lg text-muted-foreground/40" />
          <Text type="secondary" className="text-xs block mt-1">
            暂无可用的 MCP 服务
          </Text>
          <Text type="secondary" className="text-xs block mt-0.5">
            请先在 MCP 服务管理页面添加服务
          </Text>
        </div>
      ) : (
        <div className="space-y-3">
          {mcpServers.map((server) => {
            const statusColor =
              server.status === 'connected' ? 'green'
                : server.status === 'error' ? 'red'
                  : 'default';
            const statusLabel =
              server.status === 'connected' ? '已连接'
                : server.status === 'error' ? '错误'
                  : '未连接';

            // Get tool full names for this server
            const serverToolNames = (server.tools || []).map(
              (t) => `${server.tool_prefix || ''}${t.name}`
            );
            const followsAllTools = mcpAllToolsServerIds.includes(server.id);
            const selectedCount = followsAllTools
              ? serverToolNames.length
              : serverToolNames.filter((n) => enabledMcpTools.includes(n)).length;
            const allSelected = followsAllTools
              || (serverToolNames.length > 0 && selectedCount === serverToolNames.length);
            const someSelected = selectedCount > 0 && !allSelected;
            const isExpanded = expandedMcpServerIds.includes(server.id);

            const toggleServerTools = () => {
              if (allSelected) {
                // Deselect all tools from this server
                setEnabledMcpTools((prev) => prev.filter((n) => !serverToolNames.includes(n)));
                setMcpAllToolsServerIds((prev) => prev.filter((id) => id !== server.id));
              } else {
                // Follow the complete catalogue, including tools added upstream later.
                const newTools = serverToolNames.filter((n) => !enabledMcpTools.includes(n));
                setEnabledMcpTools((prev) => [...new Set([...prev, ...newTools])]);
                setMcpAllToolsServerIds((prev) => [...new Set([...prev, server.id])]);
              }
            };

            return (
              <div
                key={server.id}
                className={cn(
                  'rounded-lg border transition',
                  someSelected || allSelected
                    ? 'border-primary/40 bg-primary/5'
                    : 'border-border/50',
                  !server.is_active && 'opacity-50',
                )}
              >
                {/* Server header */}
                <div className="flex items-center gap-2 p-2.5">
                  <Checkbox
                    aria-label={`选择 ${server.name} 的全部工具`}
                    checked={allSelected}
                    indeterminate={someSelected}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => toggleServerTools()}
                  />
                  <button
                    type="button"
                    className="flex flex-1 min-w-0 items-center gap-2 rounded text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
                    aria-label={`${isExpanded ? '收起' : '展开'} ${server.name} 工具`}
                    aria-expanded={isExpanded}
                    aria-controls={`mcp-tools-${server.id}`}
                    onClick={() => setExpandedMcpServerIds((prev) =>
                      prev.includes(server.id)
                        ? prev.filter((id) => id !== server.id)
                        : [...prev, server.id]
                    )}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <Text className="text-xs font-medium truncate">{server.name}</Text>
                        <Tag color={statusColor} className="text-[10px] leading-none flex-shrink-0">
                          {statusLabel}
                        </Tag>
                      </div>
                      <Text type="secondary" className="text-[10px] block mt-0.5 truncate">
                        {server.transport_type} · {server.tools_count} 个工具
                        {selectedCount > 0 && ` · 已选 ${selectedCount}`}
                        {followsAllTools && ' · 自动包含新增工具'}
                      </Text>
                    </div>
                    <RightOutlined className={cn('text-[10px] text-muted-foreground transition-transform', isExpanded && 'rotate-90')} />
                  </button>
                </div>

                {/* Tool list */}
                <div id={`mcp-tools-${server.id}`} hidden={!isExpanded}>
                  <div className="max-h-80 overflow-y-auto border-t border-border/30 px-2 py-1.5 space-y-1">
                    {!server.tools?.length && (
                      <Text type="secondary" className="text-xs">暂无可用工具</Text>
                    )}
                    {(server.tools || []).map((tool) => {
                      const fullName = `${server.tool_prefix || ''}${tool.name}`;
                      const isChecked = followsAllTools || enabledMcpTools.includes(fullName);
                      return (
                        <div key={fullName} className="flex items-start gap-2 px-1">
                          <Checkbox
                            checked={isChecked}
                            onChange={(e) => {
                              if (e.target.checked) {
                                const nextServerTools = new Set([
                                  ...enabledMcpTools.filter((name) => serverToolNames.includes(name)),
                                  fullName,
                                ]);
                                setEnabledMcpTools((prev) => [...new Set([...prev, fullName])]);
                                if (serverToolNames.every((name) => nextServerTools.has(name))) {
                                  setMcpAllToolsServerIds((prev) => [...new Set([...prev, server.id])]);
                                }
                              } else {
                                setMcpAllToolsServerIds((prev) => prev.filter((id) => id !== server.id));
                                setEnabledMcpTools((prev) => followsAllTools
                                  ? [
                                    ...prev.filter((name) => !serverToolNames.includes(name)),
                                    ...serverToolNames.filter((name) => name !== fullName),
                                  ]
                                  : prev.filter((name) => name !== fullName)
                                );
                              }
                            }}
                          />
                          <div className="flex-1 min-w-0">
                            <Text className="text-xs">{tool.name}</Text>
                            {tool.description && (
                              <Text type="secondary" className="text-[10px] block truncate">
                                {tool.description}
                              </Text>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <SaveButtonRow
        saving={!!savingSections['mcp']}
        saved={!!savedSections['mcp']}
        onSave={() => {
          // Derive mcp_server_ids from selected MCP tools
          const selectedServerIds = mcpServers
            .filter((server) => {
              const serverToolNames = (server.tools || []).map(
                (t) => `${server.tool_prefix || ''}${t.name}`
              );
              return mcpAllToolsServerIds.includes(server.id)
                || serverToolNames.some((n) => enabledMcpTools.includes(n));
            })
            .map((s) => s.id);
          saveSection('mcp', {
            enabled_tools: [...enabledTools, ...enabledMcpTools],
            mcp_server_ids: selectedServerIds,
            mcp_all_tools_server_ids: mcpAllToolsServerIds.filter((id) => selectedServerIds.includes(id)),
          });
        }}
      />
    </div>

  );
}
