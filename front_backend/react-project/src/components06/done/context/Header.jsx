// 1. 창고에서 데이터를 꺼내오기 위해 useContext 훅을 불러옵니다.
import { useContext } from 'react'
// 2~3. 접근할 두 개의 창고(Context) 파일을 불러옵니다.
import { ThemeContext } from './ThemeContext'
import { UserContext } from './UserContext';

// 5. 부모로부터 Props를 받지 않으므로 괄호가 비어있습니다.
const Header = () => {
    // 6. [핵심] ThemeContext 창고에서 'dark' 상태만 꺼내옵니다.
    // (Header에서는 다크모드를 변경할 일이 없으므로 setDark는 꺼내오지 않았습니다. 아주 효율적이죠!)
    const {dark} = useContext(ThemeContext);
    
    // 7. [핵심] UserContext 창고에서 'user' 상태 객체를 꺼내옵니다.
    const {user} = useContext(UserContext);

    return (
        // 10. 꺼내온 dark 값이 true면 'header black', false면 'header gray'가 적용됩니다.
        <div className={`header ${dark ? 'black':'gray'}`}>
            
            {/* 11. 꺼내온 user 객체 안의 name 값을 화면에 출력합니다. 
                Footer에서 로그인/로그아웃 버튼을 누르면 이 user.name 값이 즉시 '홍길동' 혹은 '사용자'로 바뀝니다. */}
            <h1>Welcom {user.name}</h1>
        </div>
    )
}
export default Header