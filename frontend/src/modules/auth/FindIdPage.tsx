import { useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import toast from 'react-hot-toast';
import { API_BASE } from '@/utils/api';

export default function FindIdPage() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [result, setResult] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name || !email) {
      toast.error('이름과 이메일을 입력해주세요.');
      return;
    }
    setLoading(true);
    setResult(null);
    try {
      const res = await fetch(`${API_BASE}/auth/find-id`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, email }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || '아이디를 찾을 수 없습니다.');
      }
      const data = await res.json();
      setResult(data.user_id_masked);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '아이디 찾기에 실패했습니다.');
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
          <h1 className="text-2xl font-bold text-gray-900">아이디 찾기</h1>
          <p className="text-gray-500 mt-1">가입 시 등록한 이름과 이메일을 입력하세요</p>
        </div>

        <div className="card">
          {!result ? (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-base font-medium text-gray-700 mb-2">이름</label>
                <input type="text" value={name} onChange={e => setName(e.target.value)} placeholder="이름을 입력하세요" className={inputClass} />
              </div>
              <div>
                <label className="block text-base font-medium text-gray-700 mb-2">이메일</label>
                <input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="가입 시 등록한 이메일" className={inputClass} />
              </div>
              <button type="submit" disabled={loading} className="btn-primary w-full">
                {loading ? '조회 중...' : '아이디 찾기'}
              </button>
            </form>
          ) : (
            <div className="text-center py-4">
              <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-success/10 text-3xl mb-4">
                ✅
              </div>
              <p className="text-lg text-gray-700 mb-2">회원님의 아이디는</p>
              <p className="text-2xl font-bold text-primary mb-4">{result}</p>
              <p className="text-sm text-gray-400 mb-6">개인정보 보호를 위해 일부가 가려져 있습니다.</p>
              <Link to="/login" className="btn-primary w-full">로그인하러 가기</Link>
            </div>
          )}

          <div className="mt-4 text-center flex justify-center gap-4">
            <Link to="/find-password" className="text-gray-500 hover:text-primary transition text-base">비밀번호 찾기</Link>
            <span className="text-gray-300">|</span>
            <Link to="/login" className="text-gray-500 hover:text-primary transition text-base">로그인</Link>
          </div>
        </div>
      </motion.div>
    </div>
  );
}
