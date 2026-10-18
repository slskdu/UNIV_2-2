// 1. Context에서 데이터를 꺼내오기 위한 useContext 훅을 불러옵니다.
import { useContext } from 'react'
// 2~3. 접근할 두 개의 창고(테마, 유저) 파일을 불러옵니다.
import { ThemeContext } from './ThemeContext'
import { UserContext } from './UserContext'

// 5. 부모로부터 아무런 Props도 받지 않습니다! () 안이 비어있습니다.
const Footer = () => {
    // 6. [핵심] 테마 창고에서 다크모드 상태(dark)와 변경 함수(setDark)를 모두 꺼내옵니다.
    const {dark, setDark} = useContext(ThemeContext);
    // 7. [핵심] 유저 창고에서 유저 상태(user)와 변경 함수(setUser)를 모두 꺼내옵니다.
    const {user, setUser} = useContext(UserContext);
    
    return (
        // 9. 테마 상태에 맞춰 하단바의 배경색을 바꿉니다.
        <div className={`footer ${dark ? 'black':'gray'}`}>
            
            {/* 10. [초핵심] 조건부 렌더링 (삼항 연산자 활용)
                유저가 로그인 상태(user.isLogin이 true)인가? */}
            {user.isLogin ?
                // 11. (참일 때) 로그아웃 버튼을 보여줍니다. 
                // 클릭하면 상태를 비로그인(false)과 기본 이름('사용자')으로 덮어씌웁니다.
                <button onClick={()=>setUser({isLogin:false, name:'사용자'})}>로그아웃</button>
                : 
                // 13. (거짓일 때) 로그인 버튼을 보여줍니다.
                // 클릭하면 상태를 로그인(true)과 특정 이름('홍길동')으로 덮어씌웁니다.
                <button onClick={()=>setUser({isLogin:true, name:'홍길동'})}>로그인</button>
            }
            
            {/* 15. 다크모드 켜고 끄기 (상태 반전) 버튼입니다. */}
            <button onClick={()=>setDark(!dark)}>{dark ? 'Light Mode':'Dark Mode'}</button>
        </div>
    )
}
export default Footer