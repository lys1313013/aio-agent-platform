import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';

/** Redirect to login if not authenticated */
export function useRequireAuth() {
  const navigate = useNavigate();
  const { isAuthenticated, checkAuth } = useAuthStore();

  useEffect(() => {
    const ok = checkAuth();
    if (!ok) {
      navigate('/login', { replace: true });
    }
  }, [checkAuth, navigate]);

  return isAuthenticated;
}
