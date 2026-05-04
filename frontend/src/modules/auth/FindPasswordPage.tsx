import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import toast from 'react-hot-toast';
import { API_BASE } from '@/utils/api';

export default function FindPasswordPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState<'verify' | 'reset'>('verify');
  const [userId, setUserId] = useState('');
  const [email, setEmail] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [resetToken, setResetToken] = useState('');
  const [loading, setLoading] = useState(false);

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!userId || !email) {
      toast.error('아이디와 이메일을 입력해주세요.');
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/auth/find-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, email }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || '본인 확인에 실패했습니다.');
      }
      const data = await res.json();
      setResetToken(data.reset_token);
      toast.success('본인 확인이 완료되었습니다.');
      setStep('reset');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '본인 확인에 실패했습니다.');
    } finally {
      setLoading(false);
    }
  };

  const handleReset = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword.length < 4) {
      toast.error('비밀번호는 4자 이상이어야 합니다.');
      return;
    }
    if (newPassword !== confirmPassword) {
      toast.error('비밀번호가 일치하지 않습니다.');
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/auth/reset-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, new_password: newPassword, reset_token: resetToken }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || '비밀번호 변경에 실패했습니다.');
      }
      toast.success('비밀번호가 변경되었습니다. 새 비밀번호로 로그인하세요.');
      navigate('/login');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '비밀번호 변경에 실패했습니다.');
    } finally {
      setLoading(false);
    }
  };

  const inputClass = 'w-full px-4 py-3 text-lg border border-gray-300 rounded-xl focus:ring-2 focus:ring-primary focus:border-primary outline-none transition';

  return (
    <div className="min-h-screen bg-surface flex items-center justify-center p-4">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-md"
      >
        <div className="text-center mb-6">
          <h1 className="text-2xl font-bold text-gray-900">비밀번호 찾기</h1>
          <p className="text-gray-500 mt-1">
            {step === 'verify' ? '아이디와 이메일로 본인 확인을 진행합니다' : '새 비밀번호를 설정하세요'}
          </p>
        </div>

        <div className="card">
          {/* Step indicator */}
          <div className="flex items-center justify-center gap-2 mb-6">
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold ${step === 'verify' ? 'bg-primary text-white' : 'bg-success text-white'}`}>
              {step === 'verify' ? '1' : '✓'}
            </div>
            <div className="w-12 h-0.5 bg-gray-200" />
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold ${step === 'reset' ? 'bg-primary text-white' : 'bg-gray-200 text-gray-400'}`}>
              2
            </div>
          </div>

          {step === 'verify' ? (
            <form onSubmit={handleVerify} className="space-y-4">
              <div>
                <label className="block text-base font-medium text-gray-700 mb-2">아이디</label>
                <input type="text" value={userId} onChange={e => setUserId(e.target.value)} placeholder="아이디를 입력하세요" className={inputClass} />
              </div>
              <div>
                <label className="block text-base font-medium text-gray-700 mb-2">이메일</label>
                <input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="가입 시 등록한 이메일" className={inputClass} />
              </div>
              <button type="submit" disabled={loading} className="btn-primary w-full">
                {loading ? '확인 중...' : '본인 확인'}
              </button>
            </form>
          ) : (
            <form onSubmit={handleReset} className="space-y-4">
              <div>
                <label className="block text-base font-medium text-gray-700 mb-2">새 비밀번호</label>
                <input type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} placeholder="4자 이상" className={inputClass} />
              </div>
              <div>
                <label className="block text-base font-medium text-gray-700 mb-2">새 비밀번호 확인</label>
                <input type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} placeholder="비밀번호 재입력" className={inputClass} />
              </div>
              <button type="submit" disabled={loading} className="btn-primary w-full">
                {loading ? '변경 중...' : '비밀번호 변경'}
              </button>
            </form>
          )}

          <div className="mt-4 text-center flex justify-center gap-4">
            <Link to="/find-id" className="text-gray-500 hover:text-primary transition text-base">아이디 찾기</Link>
            <span className="text-gray-300">|</span>
            <Link to="/login" className="text-gray-500 hover:text-primary transition text-base">로그인</Link>
          </div>
        </div>
      </motion.div>
    </div>
  );
}
