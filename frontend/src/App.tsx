import React, { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { Login } from './pages/Login';
import { Dashboard } from './pages/Dashboard';
import { motion, AnimatePresence } from 'framer-motion';

const logoVariants = {
  initial: { scale: 0.8, opacity: 0 },
  animate: {
    scale: 1,
    opacity: 1,
    transition: { duration: 0.7, ease: 'easeOut' },
  },
  // Exit: contract slightly (anticipation) then burst outward and vanish
  exit: {
    scale: [1, 0.9, 12],
    opacity: [1, 1, 0],
    transition: {
      scale: { duration: 0.8, times: [0, 0.25, 1], ease: 'easeIn' },
      opacity: { duration: 0.8, times: [0, 0.6, 1], ease: 'easeIn' },
    },
  },
};

const SplashScreen: React.FC = () => (
  <motion.div
    initial={{ opacity: 1 }}
    exit={{ opacity: 0 }}
    // Keep the background dark while the logo explodes, then fade it out
    transition={{ opacity: { delay: 0.45, duration: 0.35, ease: 'easeInOut' } }}
    className="fixed inset-0 z-50 flex items-center justify-center bg-[#121212] overflow-hidden"
  >
    <motion.img
      src="/logo.svg"
      alt="Aum Radar"
      variants={logoVariants}
      initial="initial"
      animate="animate"
      exit="exit"
      className="w-56 drop-shadow-[0_0_40px_rgba(212,175,55,0.35)]"
    />
  </motion.div>
);

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user, loading } = useAuth();

  if (loading) return null;
  if (!user) return <Navigate to="/login" />;

  return <>{children}</>;
};

function App() {
  const [showSplash, setShowSplash] = useState(true);

  useEffect(() => {
    const timer = setTimeout(() => setShowSplash(false), 2000);
    return () => clearTimeout(timer);
  }, []);

  return (
    <>
      <AnimatePresence>
        {showSplash && <SplashScreen key="splash" />}
      </AnimatePresence>

      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/dashboard"
              element={
                <ProtectedRoute>
                  <Dashboard />
                </ProtectedRoute>
              }
            />
            <Route path="*" element={<Navigate to="/dashboard" />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </>
  );
}

export default App;
