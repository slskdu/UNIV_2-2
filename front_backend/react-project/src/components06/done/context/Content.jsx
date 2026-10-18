// 1. Context 창고에서 데이터를 꺼내오기 위해 리액트 내장 훅인 useContext를 불러옵니다.
import { useContext } from 'react'
// 2~3. 어떤 창고에서 데이터를 꺼낼지 명시하기 위해, 미리 만들어둔 창고(Context) 파일들을 불러옵니다.
import { ThemeContext } from './ThemeContext'
import { UserContext } from './UserContext';

// 5. 이전(Props 방식)과 달리 매개변수 자리가 () 로 텅 비어있습니다! 부모가 주는 데이터를 받을 필요가 없기 때문입니다.
const Content = () => {
    // 6. [초핵심] ThemeContext 창고에 접근해서 'dark' 상태만 구조 분해 할당으로 쏙 빼옵니다.
    const {dark} = useContext(ThemeContext);
    
    // 7. 마찬가지로 UserContext 창고에 접근해서 'user' 상태만 빼옵니다. (이 안에는 isLogin과 name이 들어있습니다.)
    const {user} = useContext(UserContext);
    
    return (
        // 9. 창고에서 꺼내온 dark 값을 이용해 다크모드일 땐 'black', 아닐 땐 'white' 클래스를 적용합니다.
        <div className={`content ${dark ? 'black':'white'}`}>
            
            {/* 10. 창고에서 꺼내온 user 객체 안의 name 값을 화면에 렌더링합니다. */}
            <h1>{user.name}님 좋은 하루되세요!</h1>
        </div>
    )
}
export default Content