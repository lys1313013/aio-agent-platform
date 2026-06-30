import { Link, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';
import { RobotOutlined } from '@ant-design/icons';
import { Form, Input, Button, Alert, Typography } from 'antd';

const { Title, Text } = Typography;

interface LoginForm {
  username: string;
  password: string;
}

export default function LoginPage() {
  const navigate = useNavigate();
  const { login, isLoading, error, clearError } = useAuthStore();
  const [form] = Form.useForm<LoginForm>();

  const handleSubmit = async (values: LoginForm) => {
    const ok = await login(values.username, values.password);
    if (ok) navigate('/', { replace: true });
  };

  const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    form.submit();
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm space-y-6">
        {/* Header */}
        <div className="flex flex-col items-center space-y-2">
          <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-primary/10">
            <RobotOutlined className="text-2xl text-primary" />
          </div>
          <Title level={3} className="!mb-0">
            智能体平台
          </Title>
          <Text type="secondary">登录您的账户</Text>
        </div>

        {/* Form */}
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          className="space-y-2"
        >
          {error && (
            <Alert
              message={error}
              type="error"
              closable
              onClose={clearError}
              showIcon
              className="mb-4"
            />
          )}

          <Form.Item
            name="username"
            label="用户名或邮箱"
            rules={[{ required: true, message: '请输入用户名或邮箱' }]}
          >
            <Input placeholder="alice" size="large" autoFocus />
          </Form.Item>

          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password placeholder="••••••••" size="large" />
          </Form.Item>

          <Form.Item>
            <Button
              type="primary"
              htmlType="button"
              size="large"
              loading={isLoading}
              onClick={handleClick}
              block
            >
              登录
            </Button>
          </Form.Item>
        </Form>

        <div className="text-center">
          <Text type="secondary">
            还没有账户？{' '}
            <Link to="/register">立即注册</Link>
          </Text>
        </div>
      </div>
    </div>
  );
}
