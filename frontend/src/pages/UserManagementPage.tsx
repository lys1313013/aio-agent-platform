import { useCallback, useEffect, useMemo, useState } from 'react';
import { Navigate } from 'react-router-dom';
import {
  EditOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons';
import {
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { tenantsApi, usersApi } from '@/lib/api';
import type { AdminUser, Tenant } from '@/lib/api';
import { getUserId } from '@/lib/auth';
import { useAuthStore } from '@/stores/authStore';

const { Text } = Typography;

export default function UserManagementPage() {
  const { message } = App.useApp();
  const role = useAuthStore((state) => state.role);
  const currentUserId = getUserId();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const [form] = Form.useForm();

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [userItems, tenantItems] = await Promise.all([
        usersApi.list(),
        tenantsApi.list(),
      ]);
      setUsers(userItems);
      setTenants(tenantItems);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载用户失败');
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    if (role === 'superadmin') void loadData();
  }, [role, loadData]);

  const tenantMap = useMemo(
    () => new Map(tenants.map((tenant) => [tenant.id, tenant])),
    [tenants],
  );

  if (role !== 'superadmin') {
    return <Navigate to="/" replace />;
  }

  const openModal = (user?: AdminUser) => {
    setEditingUser(user ?? null);
    if (user) {
      form.setFieldsValue({
        username: user.username,
        email: user.email,
        display_name: user.display_name,
        password: undefined,
        role: user.role,
        is_active: user.is_active,
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ role: 'user', is_active: true });
    }
    setModalOpen(true);
  };

  const saveUser = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      if (editingUser) {
        const payload: Parameters<typeof usersApi.update>[1] = {
          username: values.username,
          email: values.email,
          display_name: values.display_name || null,
          password: values.password || undefined,
        };
        if (editingUser.id !== currentUserId) {
          payload.role = values.role;
          payload.is_active = values.is_active;
        }
        await usersApi.update(editingUser.id, payload);
        message.success('用户已更新');
      } else {
        await usersApi.create({
          username: values.username,
          email: values.email,
          display_name: values.display_name || undefined,
          password: values.password,
          role: values.role,
          tenant_ids: values.tenant_ids,
          active_tenant_id: values.active_tenant_id || values.tenant_ids[0],
        });
        message.success('用户已创建');
      }
      setModalOpen(false);
      await loadData();
    } catch (error: any) {
      if (!error?.errorFields) {
        message.error(error instanceof Error ? error.message : '保存失败');
      }
    } finally {
      setSaving(false);
    }
  };

  const columns: ColumnsType<AdminUser> = [
    {
      title: '用户',
      key: 'user',
      render: (_, user) => (
        <div>
          <div className="font-medium text-foreground">
            {user.display_name || user.username}
            {user.display_name && (
              <Text type="secondary" className="ml-2 text-xs">@{user.username}</Text>
            )}
          </div>
          <Text type="secondary" className="text-xs">{user.email}</Text>
        </div>
      ),
    },
    {
      title: '所属租户',
      key: 'tenants',
      render: (_, user) => (
        <Space size={[4, 4]} wrap>
          {user.tenant_ids.map((tenantId) => {
            const tenant = tenantMap.get(tenantId);
            return (
              <Tag key={tenantId} color={tenantId === user.active_tenant_id ? 'blue' : 'default'}>
                {tenant?.name || tenantId}
                {tenantId === user.active_tenant_id ? ' · 当前' : ''}
              </Tag>
            );
          })}
        </Space>
      ),
    },
    {
      title: '角色',
      dataIndex: 'role',
      width: 120,
      render: (userRole: AdminUser['role']) => (
        <Tag color={userRole === 'superadmin' ? 'purple' : userRole === 'admin' ? 'blue' : 'default'}>
          {userRole === 'superadmin' ? '超级管理员' : userRole === 'admin' ? '管理员' : '用户'}
        </Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 100,
      render: (active: boolean) => (
        <Tag color={active ? 'success' : 'default'}>{active ? '正常' : '已禁用'}</Tag>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 80,
      render: (_, user) => (
        <Tooltip title="编辑用户">
          <Button
            type="text"
            icon={<EditOutlined />}
            aria-label={`编辑用户 ${user.username}`}
            onClick={() => openModal(user)}
          />
        </Tooltip>
      ),
    },
  ];

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
        <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-2xl font-bold text-foreground">用户管理</h1>
            <Text type="secondary">创建平台用户并维护账号资料、角色和状态</Text>
          </div>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>
            新建用户
          </Button>
        </div>

        <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-3">
          <Card size="small">
            <div className="flex items-center gap-3 p-1">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <TeamOutlined />
              </div>
              <div>
                <div className="text-xl font-semibold tabular-nums">{users.length}</div>
                <Text type="secondary" className="text-xs">平台用户</Text>
              </div>
            </div>
          </Card>
          <Card size="small">
            <div className="flex items-center gap-3 p-1">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <UserOutlined />
              </div>
              <div>
                <div className="text-xl font-semibold tabular-nums">
                  {users.filter((user) => user.is_active).length}
                </div>
                <Text type="secondary" className="text-xs">启用用户</Text>
              </div>
            </div>
          </Card>
          <Card size="small" className="col-span-2 lg:col-span-1">
            <div className="flex items-center gap-3 p-1">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <SafetyCertificateOutlined />
              </div>
              <div>
                <div className="text-xl font-semibold tabular-nums">
                  {users.filter((user) => user.role !== 'user').length}
                </div>
                <Text type="secondary" className="text-xs">管理员</Text>
              </div>
            </div>
          </Card>
        </div>

        <Card>
          {loading ? (
            <div className="flex min-h-64 items-center justify-center"><Spin size="large" /></div>
          ) : users.length === 0 ? (
            <Empty description="暂无用户">
              <Button type="primary" onClick={() => openModal()}>新建用户</Button>
            </Empty>
          ) : (
            <Table
              rowKey="id"
              columns={columns}
              dataSource={users}
              pagination={{ pageSize: 10, showSizeChanger: true }}
              scroll={{ x: 860 }}
            />
          )}
        </Card>
      </div>

      <Modal
        title={editingUser ? '编辑用户' : '新建用户'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => void saveUser()}
        confirmLoading={saving}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" className="mt-4">
          <Form.Item name="display_name" label="显示名称">
            <Input placeholder="可选" />
          </Form.Item>
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }, { min: 3 }]}
          >
            <Input autoFocus autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="email"
            label="邮箱"
            rules={[{ required: true, message: '请输入邮箱' }, { type: 'email' }]}
          >
            <Input type="email" autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="password"
            label={editingUser ? '重置密码' : '初始密码'}
            extra={editingUser ? '留空则保持当前密码不变' : undefined}
            rules={editingUser ? [{ min: 8 }] : [{ required: true }, { min: 8 }]}
          >
            <Input.Password autoComplete="new-password" placeholder={editingUser ? '留空不修改' : ''} />
          </Form.Item>
          {!editingUser && (
            <>
              <Form.Item
                name="tenant_ids"
                label="所属租户"
                rules={[{ required: true, message: '请至少选择一个租户' }]}
              >
                <Select
                  mode="multiple"
                  showSearch
                  optionFilterProp="label"
                  options={tenants
                    .filter((tenant) => tenant.is_active)
                    .map((tenant) => ({ value: tenant.id, label: tenant.name }))}
                />
              </Form.Item>
              <Form.Item
                noStyle
                shouldUpdate={(previous, current) => previous.tenant_ids !== current.tenant_ids}
              >
                {({ getFieldValue }) => {
                  const selectedTenantIds: string[] = getFieldValue('tenant_ids') || [];
                  return selectedTenantIds.length > 1 ? (
                    <Form.Item name="active_tenant_id" label="初始当前租户">
                      <Select
                        options={selectedTenantIds.map((tenantId) => ({
                          value: tenantId,
                          label: tenantMap.get(tenantId)?.name || tenantId,
                        }))}
                      />
                    </Form.Item>
                  ) : null;
                }}
              </Form.Item>
            </>
          )}
          <Form.Item name="role" label="角色">
            <Select
              disabled={editingUser?.id === currentUserId}
              options={[
                { value: 'user', label: '用户' },
                { value: 'admin', label: '管理员' },
                { value: 'superadmin', label: '超级管理员' },
              ]}
            />
          </Form.Item>
          {editingUser && (
            <>
              <Form.Item name="is_active" label="启用用户" valuePropName="checked">
                <Switch disabled={editingUser.id === currentUserId} />
              </Form.Item>
              <Text type="secondary" className="text-xs">
                用户的租户成员关系请在“租户管理”中调整。
              </Text>
            </>
          )}
        </Form>
      </Modal>
    </div>
  );
}
